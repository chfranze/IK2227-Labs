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

def read_weights(iface: str) -> bytes:
    """
    Establish RDMA connection and read LLama2 weights from llama_weights server.
    
    Args:
        iface: Name of the SoftRoCE interface (e.g., 'rxe1')
    
    Returns:
        bytes: The LLama2 weights read from remote memory
    """
    # Step 1: Verify RDMA device exists
    logging.info(f"Searching for RDMA device: {iface}")
    devices_list = rdma_get_devices()
    found = False
    for device in devices_list:
        device_name_str = device.name.decode('utf-8')
        logging.info(f"Found RDMA device: {device_name_str}")
        if device_name_str == iface:
            found = True
            break
    
    if not found:
        raise Exception(f"Interface {iface} not found.")
    
    # Step 2: Open TCP connection to server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        logging.info(f"Connecting to llama_weights server at 3.0.0.1:{PORT}")
        sock.connect(("3.0.0.1", PORT))
        logging.info("TCP connection established")
    except Exception as ex:
        logging.error(f"Error connecting to server: {ex}")
        sock.close()
        raise
    
    try:
        # Step 3: Create RDMA context and resources
        with pyverbs.device.Context(name=iface) as ctx:
            logging.info(f"RDMA context opened on {iface}")
            
            # Create Protection Domain
            pd = pyverbs.pd.PD(ctx)
            logging.info("Protection domain created")
            
            # Register memory region for local write access
            mr = pyverbs.mr.MR(pd, BUFFER_SIZE, IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_READ)
            logging.info(f"Memory registered: addr={mr.buf}, rkey={mr.rkey}, length={BUFFER_SIZE}")
            
            # Create Completion Queue
            cq = pyverbs.cq.CQ(ctx, 10)
            logging.info("Completion Queue created")
            
            # Create Queue Pair
            qp_init_attr = pyverbs.qp.QPInitAttr(
                qp_type=IBV_QPT_RC,
                scq=cq,
                rcq=cq,
                cap=pyverbs.qp.QPCap(
                    max_send_wr=1,
                    max_recv_wr=1,
                    max_send_sge=1,
                    max_recv_sge=1
                )
            )
            qp = pyverbs.qp.QP(pd, qp_init_attr)
            logging.info(f"QP created with qp_num={qp.qp_num}")
            
            # Step 4: Transition QP to INIT state
            init_attr = pyverbs.qp.QPAttr(qp_state=IBV_QPS_INIT, port_num=1)
            init_attr.pkey_index = 0
            init_attr.qp_access_flags = IBV_ACCESS_REMOTE_READ | IBV_ACCESS_LOCAL_WRITE
            
            qp.modify(
                init_attr,
                IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS
            )
            logging.info("QP moved to INIT state")
            
            # Step 5: Exchange connection information with server
            # Receive server info first
            remote_data = recvn(sock, ctypes.sizeof(QpConnectionData))
            if len(remote_data) != ctypes.sizeof(QpConnectionData):
                raise Exception("Failed to receive complete server connection data")
            
            remote = QpConnectionData.from_buffer_copy(remote_data)
            logging.info(f"Received server info: qp_num={remote.qp_num}, rkey={remote.rkey}, "
                        f"addr={remote.addr}, gid={bytes(remote.gid).hex()}")
            
            # Get local GID
            local_gid = ctx.query_gid(port_num=1, index=1)
            byte_gid = ipaddress.ip_address(local_gid.gid).packed
            logging.info(f"Local GID: {ipaddress.ip_address(local_gid.gid).exploded}")
            logging.info(f"Server GID: {ipaddress.ip_address(bytes(remote.gid)).exploded}")
            
            # Send client info to server
            local_con_obj = QpConnectionData()
            local_con_obj.qp_num = qp.qp_num
            local_con_obj.rkey = mr.rkey
            local_con_obj.addr = mr.buf
            local_con_obj.gid[:] = byte_gid
            sock.sendall(bytes(local_con_obj))
            logging.info("Client connection info sent")
            
            # Step 6: Transition QP to RTR (Ready-To-Receive)
            rtr_attr = pyverbs.qp.QPAttr(qp_state=IBV_QPS_RTR, path_mtu=IBV_MTU_1024)
            rtr_attr.dest_qp_num = remote.qp_num
            rtr_attr.rq_psn = 0
            rtr_attr.max_dest_rd_atomic = 1
            rtr_attr.min_rnr_timer = 31
            
            remote_gid_str = ipaddress.ip_address(bytes(remote.gid)).exploded
            gr = pyverbs.addr.GlobalRoute(
                dgid=pyverbs.addr.GID(val=remote_gid_str),
                sgid_index=1
            )
            rtr_attr.ah_attr = pyverbs.addr.AHAttr(gr=gr, is_global=1, port_num=1)
            
            qp.modify(
                rtr_attr,
                IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN | IBV_QP_RQ_PSN |
                IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER | IBV_QP_AV
            )
            logging.info("QP moved to RTR")
            
            # Step 7: Transition QP to RTS (Ready-To-Send)
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
            logging.info("QP moved to RTS - connection established!")
            
            # Step 8: Start CQ polling thread
            global keep_polling
            keep_polling = True
            poller = threading.Thread(target=poll_cq, args=(cq, mr))
            poller.start()
            logging.info("CQ polling thread started")
            
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
            wr.set_wr_rdma(rkey=remote.rkey, addr=remote.addr)
            
            logging.info(f"Posting RDMA READ: remote_addr={remote.addr}, rkey={remote.rkey}, "
                        f"length={BUFFER_SIZE}")
            qp.post_send(wr)
            logging.info("RDMA READ posted, waiting for completion...")
            
            # Step 10: Wait for completion
            poller.join()
            logging.info("RDMA READ operation completed successfully")
            
            # Step 11: Read and return the weights from local memory
            weights = mr.read(BUFFER_SIZE, 0)
            logging.info(f"Read {len(weights)} bytes from memory region")
            
            return weights
            
    except Exception as e:
        logging.error(f"RDMA operation failed: {e}")
        raise
    finally:
        sock.close()
        keep_polling = False
        logging.info("RDMA session finished, TCP connection closed")