import ctypes
import ipaddress
import logging
import os
import pyverbs
import socket
import struct
import sys
import threading
import time

from pyverbs.device import rdma_get_devices
from pyverbs.enums import *


PORT = 12345
BUFFER_SIZE = 60816028


class QpConnectionData(ctypes.BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('qp_num', ctypes.c_uint32),
        ('rkey',   ctypes.c_uint32),
        ('addr',   ctypes.c_uint64),
        ('gid',    ctypes.c_ubyte * 16)
    ]


callback = None
keep_polling: bool = True

# Thread for polling the Completion Queue 
def poll_cq(cq: pyverbs.cq.CQ, mr: pyverbs.mr.MR) -> None:
    global keep_polling

    while keep_polling:
        wc_num, wc_list = cq.poll(num_entries=1)
        if wc_num > 0:
            for wc in wc_list:
                logging.info(f"CQE: wr_id={wc.wr_id}, status={wc.status}")
                if wc.wr_id == 0xdead:
                    if callback is not None:
                        callback(mr.read(length=BUFFER_SIZE, offset=0))
                    keep_polling = False

# Helper to receive exactly n bytes from the socket
def recvn(sock: socket.socket, n: int) -> bytes:
    data = b''

    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    
    return data

# The SoftRoCE interface name is passed as argument
# The PORT constant is the TCP port where the server is listening
def read_weights(iface: str) -> None:
    # Get all available RDMA-compatible devices
    devices_list = pyverbs.device.rdma_get_devices()
    found = False
    for device in devices_list:
        # device.name is of "byte" type, let's convert to string
        device_name_str = device.name.decode('utf-8')
        if device_name_str == iface:
            found = True
            break
    if not found:
        raise Exception(f"Interface {iface} not found.")
    
    # 1. Initialize TCP connection
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("3.0.0.1", PORT))
    except Exception as ex:
        print(f"Error connecting to server: {ex}", file=sys.stderr)
        # sys.exit(1)
        sock.close()

    try:
        # 2. Setup RDMA Resources 
        with pyverbs.device.Context(name=iface) as ctx:
            with pyverbs.pd.PD(ctx) as pd: # Create Protection Domain
                # Register memory region for local write access
                mr = pyverbs.mr.MR(
                    pd, 
                    BUFFER_SIZE, 
                    IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_READ
                )

                # Create the Completion Queue
                # The capacity is 10 CQE
                cq = pyverbs.cq.CQ(ctx, 10, None, None, 0)

                # Configure and initialize the Queue Pair
                qp_init_attr = pyverbs.qp.QPInitAttr(
                    qp_type=IBV_QPT_RC,       # Type of QP (RC=Reliable Connection)
                    scq=cq,                   # CQ to associate with the Send Queue
                    rcq=cq,                   # CQ to associate with the Receive Queue
                    cap=pyverbs.qp.QPCap(
                        max_send_wr=8192, # Max num of outstanding WRs that can be posted to the Send Queue (varies per RNIC)
                        max_recv_wr=8192, # Max num of outstanding WRs that can be posted to the Receive Queue (varies per RNIC)
                        max_send_sge=32,  # Max num of scatter/gather elements in any WR that can be posted to the Send Queue (varies per RNIC)
                        max_recv_sge=32   # Max num of scatter/gather elements in any WR that can be posted to the Receive Queue (varies per RNIC)
                    )
                )

                # Create the QP
                qp = pyverbs.qp.QP(pd, qp_init_attr)

                # 3. QP State Transition: INIT
                attr = pyverbs.qp.QPAttr(qp_state=IBV_QPS_INIT, port_num=1)
                attr.pkey_index = 0   # Set the default partition keys table for this QP
                attr.qp_access_flags = IBV_ACCESS_REMOTE_READ | IBV_ACCESS_LOCAL_WRITE 
                
                # Move the QP in the INIT state
                qp.modify(
                    attr, 
                    IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS
                )

                # 4. xchange connection information with server
                remote_data = recvn(sock, ctypes.sizeof(QpConnectionData))
                remote_con = QpConnectionData.from_buffer_copy(remote_data)

                # Get local GID
                local_gid = ctx.query_gid(port_num=1, index=1)
                byte_gid = ipaddress.ip_address(local_gid.gid).packed
                
                # Send client info to server
                local_con_obj = QpConnectionData()
                local_con_obj.qp_num = qp.qp_num
                local_con_obj.rkey = mr.rkey
                local_con_obj.addr = mr.buf
                local_con_obj.gid[:] = byte_gid
                sock.sendall(bytes(local_con_obj))

                # 5. RTR -> RTS Transitions
                remote_gid_str = ipaddress.ip_address(bytes(remote_con.gid)).exploded
                gr = pyverbs.addr.GlobalRoute(
                    dgid=pyverbs.addr.GID(val=remote_gid_str), 
                    sgid_index=1
                )
                ah_attr = pyverbs.addr.AHAttr(gr=gr, is_global=1, port_num=1)

                # Transition QP to INIT state
                rtr_attr = pyverbs.qp.QPAttr(
                    qp_state=IBV_QPS_RTR, 
                    path_mtu=IBV_MTU_1024
                )

                rtr_attr.dest_qp_num = remote_con.qp_num
                rtr_attr.rq_psn = 0
                rtr_attr.max_dest_rd_atomic = 1
                rtr_attr.min_rnr_timer = 31
                rtr_attr.ah_attr = ah_attr  

                # RTR MODIFICATION 
                qp.modify(
                    rtr_attr,
                    IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN | IBV_QP_RQ_PSN |
                    IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER | IBV_QP_AV
                )

                # RTS MODIFICATION
                rts_attr = pyverbs.qp.QPAttr(qp_state=IBV_QPS_RTS)
                rts_attr.sq_psn = 0
                rts_attr.timeout = 14
                rts_attr.retry_cnt = 7
                rts_attr.rnr_retry = 7
                rts_attr.max_rd_atomic = 1

                qp.modify(
                    rts_attr,
                    IBV_QP_STATE | IBV_QP_TIMEOUT | IBV_QP_RETRY_CNT |
                    IBV_QP_RNR_RETRY | IBV_QP_SQ_PSN | IBV_QP_MAX_QP_RD_ATOMIC
                )

                # 6. Post READ Request 
                global keep_polling
                keep_polling = True
                cq_thread = threading.Thread(target=poll_cq, args=(cq, mr))
                cq_thread.start()

                # Step 9: Post RDMA READ request
                sge = pyverbs.wr.SGE(
                    addr=int(mr.buf),
                    length=BUFFER_SIZE,
                    lkey=mr.lkey
                )
                
                wr = pyverbs.wr.SendWR(
                    wr_id=0xdead,
                    sg=[sge],
                    num_sge=1,
                    opcode=IBV_WR_RDMA_READ
                )
                
                # Set remote memory address and key
                wr.set_wr_rdma(rkey=remote_con.rkey, addr=remote_con.addr)
                qp.post_send(wr)

                # Wait for the CQE to be processed by the polling thread... complition
                cq_thread.join()
                keep_polling = False

                # Read and return the weights from local memory
                weights= mr.read(length=BUFFER_SIZE, offset=0)
                
    finally:
        sock.close()

    return weights