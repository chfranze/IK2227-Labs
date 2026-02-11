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

from ctypes import c_void_p
from pyverbs.device import rdma_get_devices
from pyverbs.enums import *

PORT = 12345
BUFFER_SIZE = 60816028

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(filename)s:%(lineno)d: %(message)s'
)

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

    logging.info("CQ poller started")
    while keep_polling:
        wc_num, wc_list = cq.poll(num_entries=1)
        if wc_num == 0:
            logging.debug("CQ poll: no completions yet")
        else:
            logging.info(f"CQ poll: {wc_num} completions")
            for wc in wc_list:
                logging.info(f"CQE: wr_id={wc.wr_id}, status={wc.status}")
                if wc.wr_id == 0xdead:
                    logging.info("RDMA READ completed!")
                    if callback is not None:
                        callback(mr.read(length=BUFFER_SIZE, offset=0))
                    keep_polling = False
        time.sleep(0.01)
    logging.info("CQ poller exiting")

def recvn(sock: socket.socket, n: int) -> bytes:
    data = b''
    logging.info(f"Receiving {n} bytes from socket")
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            logging.warning("Socket closed while receiving data")
            break
        data += chunk
    logging.info(f"Received {len(data)} bytes from socket")
    return data

def read_weights(iface: str) -> None:
    logging.info(f"Searching for RDMA device: {iface}")
    devices_list = pyverbs.device.rdma_get_devices()
    found = False
    for device in devices_list:
        device_name_str = device.name.decode('utf-8')
        logging.info(f"Found RDMA device: {device_name_str}")
        if device_name_str == iface:
            found = True
            break
    if not found:
        raise Exception(f"Interface {iface} not found.")

    # Open TCP connection
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        logging.info(f"Opening TCP socket to server at 3.0.0.1:{PORT}")
        sock.connect(("3.0.0.1", PORT))
        logging.info("TCP connection established")
    except Exception as ex:
        logging.error(f"Error connecting to server: {ex}")
        sys.exit(1)

    # RDMA context
    with pyverbs.device.Context(name=iface) as ctx:
        logging.info(f"RDMA context opened on {iface}")
        pd = pyverbs.pd.PD(ctx)
        logging.info("Protection domain created")

        mr = pyverbs.mr.MR(pd, BUFFER_SIZE, IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_READ)
        logging.info(f"Memory registered: addr={mr.buf}, rkey={mr.rkey}, length={BUFFER_SIZE}")

        cq = pyverbs.cq.CQ(ctx, 1)
        logging.info("Completion Queue created")

        qp = pyverbs.qp.QP(
            pd,
            pyverbs.qp.QPInitAttr(qp_type=IBV_QPT_RC, scq=cq, rcq=cq, cap=pyverbs.qp.QPCap(1, 1, 1, 1))
        )
        logging.info(f"QP created with qp_num={qp.qp_num}")

        # INIT
        # Create QPAttr for INIT
        init_attr = pyverbs.qp.QPAttr(qp_state=IBV_QPS_INIT, port_num=1)
        init_attr.pkey_index = 0
        init_attr.qp_access_flags = IBV_ACCESS_REMOTE_READ | IBV_ACCESS_LOCAL_WRITE

        # Apply modifications
        qp.modify(
            init_attr,
            IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS
        )
        logging.info("QP moved to INIT state")

        # Receive server info
        remote = QpConnectionData.from_buffer_copy(recvn(sock, ctypes.sizeof(QpConnectionData)))
        logging.info(f"Received server info: qp_num={remote.qp_num}, rkey={remote.rkey}, addr={remote.addr}, gid={bytes(remote.gid).hex()}")

        # Check for QP number collision
        if remote.qp_num == qp.qp_num:
            logging.warning("Client and server QP numbers are identical! RDMA will fail.")

        # Log GID info
        local_gid = ctx.query_gid(port_num=1, index=1)
        logging.info(f"Local GID: {ipaddress.ip_address(local_gid.gid).exploded}")
        logging.info(f"Server GID: {ipaddress.ip_address(bytes(remote.gid)).exploded}")

        # Send client info
        byte_gid = ipaddress.ip_address(local_gid.gid).packed
        local_con_obj = QpConnectionData()
        local_con_obj.qp_num = qp.qp_num + 1
        local_con_obj.rkey = mr.rkey
        local_con_obj.addr = mr.buf
        local_con_obj.gid[:] = byte_gid
        sock.sendall(bytes(local_con_obj))
        logging.info("Client connection info sent")

        # Move QP to RTR
        rtr_attr = pyverbs.qp.QPAttr(qp_state=IBV_QPS_RTR, path_mtu=IBV_MTU_1024)
        rtr_attr.dest_qp_num = remote.qp_num
        rtr_attr.rq_psn = 0
        rtr_attr.max_dest_rd_atomic = 1
        rtr_attr.min_rnr_timer = 31
        remote_gid_str = ipaddress.ip_address(bytes(remote.gid)).exploded
        gr = pyverbs.addr.GlobalRoute(dgid=pyverbs.addr.GID(val=remote_gid_str), sgid_index=1)
        rtr_attr.ah_attr = pyverbs.addr.AHAttr(gr=gr, is_global=1, port_num=1)
        qp.modify(rtr_attr, IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN |
                  IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER | IBV_QP_AV)
        logging.info("QP moved to RTR")

        # Move QP to RTS
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
        logging.info("QP moved to RTS")

        # Start polling
        global keep_polling
        keep_polling = True
        poller = threading.Thread(target=poll_cq, args=(cq, mr))
        poller.start()
        logging.info("CQ polling thread started")

        # Post RDMA READ
        sge = pyverbs.wr.SGE(
            addr=int(mr.buf),      # integer address
            length=BUFFER_SIZE,
            lkey=mr.lkey
        )

        wr = pyverbs.wr.SendWR(
            wr_id=0xdead,
            sg=[sge],
            num_sge=1,
            opcode=IBV_WR_RDMA_READ,
        )

        logging.info(f"Posting RDMA READ: remote_addr={remote.addr}, rkey={remote.rkey}")
        qp.post_send(wr)
        logging.info("RDMA READ posted, waiting for completion...")

        # Keep alive for CQ polling
        try:
            while keep_polling:
                time.sleep(0.01)
        except KeyboardInterrupt:
            keep_polling = False
        poller.join()
        sock.close()
        logging.info("RDMA session finished")
        keep_polling = False

        return mr.read(BUFFER_SIZE, 0)
