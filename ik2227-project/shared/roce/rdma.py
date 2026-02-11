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
    devices_list = pyverbs.device.rdma_get_devices()
    found = False
    for device in devices_list:
        device_name_str = device.name.decode('utf-8')
        if device_name_str == iface:
            found = True
            break
    if not found:
        raise Exception(f"Interface {iface} not found.")

    # TODO: Write your logic here!
    
    # Connection parameters exchange must be reliable, so open a TCP socket to the server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        logging.info("Socket: connecting to llama_weights at 3.0.0.1")
        sock.connect(("3.0.0.1", PORT))
        logging.info("Socket: TCP connection established")
    except Exception as ex:
        print("Error connecting to llama_weights:", ex, file=sys.stderr)
        sys.exit(1)


    # Connected to server
    # Open a context for the RDMA device
    with pyverbs.device.Context(name=iface) as ctx:
        # Create a protection domain
        pd = pyverbs.pd.PD(ctx)

        # Register local memory (destination of RDMA READ)
        mr = pyverbs.mr.MR(
            pd,
            BUFFER_SIZE,
            IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_READ
        )
        logging.info("RDMA: memory registered")

        # Completion Queue (we expect only one CQE)
        cq = pyverbs.cq.CQ(ctx, 1)

        # Create Reliable Connection Queue Pair
        qp = pyverbs.qp.QP(
            pd,
            pyverbs.qp.QPInitAttr(
                qp_type=IBV_QPT_RC,
                scq=cq,
                rcq=cq,
                cap=pyverbs.qp.QPCap(1, 1, 1, 1)
            )
        )
        logging.info("RDMA: QP created")

        # First, we can move into the INIT state, we do not need any remote information
        init_attr = pyverbs.qp.QPAttr(
            qp_state=IBV_QPS_INIT,
            port_num=1
        )
        init_attr.pkey_index = 0
        init_attr.qp_access_flags = IBV_ACCESS_REMOTE_READ | IBV_ACCESS_LOCAL_WRITE

        qp.modify(
            init_attr,
            # This specifies which fields are modifying on the QP
            IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS
        )
        
        logging.info("RDMA: QP moved to INIT")

        # --- Receive server connection info ---
        remote = QpConnectionData.from_buffer_copy(
            recvn(sock, ctypes.sizeof(QpConnectionData))
        )
        logging.info("RDMA: received server connection info")

        # --- Send client connection info ---
        # Query local GID for RoCEv2 (port=1, GID index=1)
        local_gid = ctx.query_gid(port_num=1, index=1)
        # Convert to bytes for QpConnectionData
        byte_gid = ipaddress.ip_address(local_gid.gid).packed

        local_con_obj = QpConnectionData()
        local_con_obj.qp_num = qp.qp_num
        local_con_obj.rkey = mr.rkey
        local_con_obj.addr = mr.buf
        local_con_obj.gid[:] = byte_gid
        
        sock.sendall(bytes(local_con_obj))
        logging.info("RDMA: sent client connection info")

        # --- Move QP to RTR ---
        rtr_attr = pyverbs.qp.QPAttr(
            qp_state=IBV_QPS_RTR,
            path_mtu=IBV_MTU_1024
        )
        rtr_attr.dest_qp_num = remote.qp_num
        rtr_attr.rq_psn = 0
        rtr_attr.max_dest_rd_atomic = 1
        rtr_attr.min_rnr_timer = 31
        
        # GlobalRoute requires the GID in string format
        remote_gid_str = ipaddress.ip_address(bytes(remote.gid)).exploded
        gr = pyverbs.addr.GlobalRoute(
            dgid=pyverbs.addr.GID(val=remote_gid_str),
            sgid_index=1
        )
        ah_attr = pyverbs.addr.AHAttr(
            gr=gr,
            is_global=1,
            port_num=1
        )
        rtr_attr.ah_attr = ah_attr
        
        qp.modify(
            rtr_attr,
            IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN |
            IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER | IBV_QP_AV
        )
        logging.info("RDMA: QP moved to RTR")
        
        # --- Move QP to RTS ---
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
        logging.info("RDMA: QP moved to RTS")
        
        # --- Start CQ polling thread ---
        global keep_polling
        keep_polling = True

        poller = threading.Thread(
            target=poll_cq,
            args=(cq, mr)
        )
        poller.start()

        sge = pyverbs.wr.SGE(
            mr.buf,
            BUFFER_SIZE,
            mr.lkey
        )

        wr = pyverbs.wr.SendWR(
            wr_id=0xdead,
            sg=[sge],
            opcode=IBV_WR_RDMA_READ,
            send_flags=IBV_SEND_SIGNALED
        )

        wr.wr.rdma.rkey = remote.rkey
        wr.wr.rdma.remote_addr = remote.addr

        qp.post_send(wr)

        while keep_polling:
            time.sleep(0.01)

        poller.join()

        keep_polling = False
        sock.close()

        return mr.read(BUFFER_SIZE, 0)
