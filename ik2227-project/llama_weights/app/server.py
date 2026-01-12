import os
import ctypes
import ipaddress
import logging
import socket
import sys
import threading

import pyverbs
from pyverbs.device import rdma_get_devices
from pyverbs.enums import *


class QpConnectionData(ctypes.BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('qp_num', ctypes.c_uint32),
        ('rkey',   ctypes.c_uint32),
        ('addr',   ctypes.c_uint64),
        ('gid',    ctypes.c_ubyte * 16)
    ]


PORT = 12345
BUFFER_SIZE = 60816028
CHKP_PATH = "/shared/bin/stories15M.bin"


def recvn(sock: socket.socket, n: int) -> bytes:
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def handle_connection(conn: socket.socket, addr, ctx, pd, mr) -> None:
    try:
        logging.info(f"RDMA Server: Connection from {addr[0]}")
        
        cq = pyverbs.cq.CQ(ctx, 10, None, None, 0)

        qp_init_attr = pyverbs.qp.QPInitAttr(
            qp_type=IBV_QPT_RC,       
            scq=cq,                   
            rcq=cq,                   
            cap=pyverbs.qp.QPCap(
                max_send_wr=8192, 
                max_recv_wr=8192, 
                max_send_sge=32,  
                max_recv_sge=32   
            )
        )
        qp = pyverbs.qp.QP(pd, qp_init_attr)

        init_attr = pyverbs.qp.QPAttr(
            qp_state=IBV_QPS_INIT,
            port_num=1                                 
        )
        init_attr.pkey_index = 0                       
        init_attr.qp_access_flags = IBV_ACCESS_REMOTE_READ                 
        
        qp.modify(
            init_attr, 
            IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS
        )
        
        local_con_obj = QpConnectionData()
        local_con_obj.qp_num = qp.qp_num
        local_con_obj.rkey = mr.rkey
        local_con_obj.addr = mr.buf
        
        local_gid = ctx.query_gid(port_num=1, index=1)
        byte_gid = ipaddress.ip_address(local_gid.gid).packed
        local_con_obj.gid[:] = byte_gid
        
        conn.sendall(bytes(local_con_obj))
        
        remote_data = recvn(conn, ctypes.sizeof(QpConnectionData))
        if len(remote_data) != ctypes.sizeof(QpConnectionData):
            logging.error("RDMA Server: Error receiving connection info from client", file=sys.stderr)
            conn.close()
            return
        
        remote_con = QpConnectionData.from_buffer_copy(remote_data)
        
        rtr_attr = pyverbs.qp.QPAttr(
            qp_state=IBV_QPS_RTR,
            path_mtu=IBV_MTU_1024             
        )
        rtr_attr.dest_qp_num = remote_con.qp_num            
        rtr_attr.rq_psn = 0                                 
        rtr_attr.max_dest_rd_atomic = 1                     
        rtr_attr.min_rnr_timer = 31                         
        
        remote_gid_str = ipaddress.ip_address(bytes(remote_con.gid)).exploded
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
            IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN | IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER | IBV_QP_AV
        )
        
        rts_attr = pyverbs.qp.QPAttr(
            qp_state=IBV_QPS_RTS
        )
        rts_attr.sq_psn = 0              
        rts_attr.timeout = 14            
        rts_attr.retry_cnt = 7           
        rts_attr.rnr_retry = 7           
        rts_attr.max_rd_atomic = 1       

        qp.modify(
            rts_attr, 
            IBV_QP_STATE | IBV_QP_TIMEOUT | IBV_QP_RETRY_CNT | IBV_QP_RNR_RETRY | IBV_QP_SQ_PSN | IBV_QP_MAX_QP_RD_ATOMIC
        )

        logging.info(f"RDMA Server: RDMA connection with {addr[0]} established successfully!")

        data = conn.recv(1)
        if not data:
            logging.info(f"RDMA Server: Client {addr[0]} has disconnected.")
            conn.close()
    except Exception as e:
        logging.error(f"RDMA Server: Error handling connection from {addr[0]}: {e}", file=sys.stderr)


def main(iface: str) -> None:
    devices_list = rdma_get_devices()
    found = False
    for device in devices_list:
        device_name_str = device.name.decode('utf-8')
        if device_name_str == iface:
            found = True
            break
    if not found:
        logging.info(f"RDMA Server: Interface {iface} not found.", file=sys.stderr)
        sys.exit(1)

    ctx = pyverbs.device.Context(name=iface)
    pd = pyverbs.pd.PD(ctx)
    mr = pyverbs.mr.MR(pd, BUFFER_SIZE, IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_READ)

    logging.info(f"RDMA Server: Loading weights checkpoint {CHKP_PATH}...")
    size = os.path.getsize(CHKP_PATH)
    with open(CHKP_PATH, "rb") as file:
        mr.write(file.read(), length=size, offset=0)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(('', PORT))
    logging.info("RDMA Server: Listening for connections.")
    server_sock.listen(1)

    try:
        while True:
            conn, addr = server_sock.accept()

            thread = threading.Thread(
                target=handle_connection,
                args=(conn, addr, ctx, pd, mr),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        pass
    finally:
        server_sock.close()
        pd.close()
        ctx.close()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        logging.error("Usage: server.py <iface>")
        sys.exit(1)
    
    main(sys.argv[1])
