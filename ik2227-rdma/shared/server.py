import ctypes
import ipaddress
import pyverbs
import socket
import struct
import sys
import threading
import time

from pyverbs.device import rdma_get_devices
from pyverbs.enums import *


# Structure for exchanging connection data with the client
class QpConnectionData(ctypes.BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('qp_num', ctypes.c_uint32),
        ('rkey',   ctypes.c_uint32),
        ('addr',   ctypes.c_uint64),
        ('gid',    ctypes.c_ubyte * 16)
    ]


# Constants
PORT = 18515
BUFFER_SIZE = 4096


# Thread for polling the Completion Queue
keep_polling = True
def poll_cq(cq: pyverbs.cq.CQ, mr: pyverbs.mr.MR) -> None:
    global keep_polling

    while keep_polling:
        # "poll" returns a tuple (number of read elements, CQE list)
        wc_num, wc_list = cq.poll(num_entries=1)
        if wc_num > 0:
            for wc in wc_list:
                # Here we just print the CQE, we can trigger application logic depending on the WR ID
                print(f"CQE: wr_id={wc.wr_id}, status={wc.status}")
                dump_mr(mr)


# Helper to dump the strings in the memory region
def dump_mr(mr: pyverbs.mr.MR) -> None:
    msg_bytes = mr.read(length=BUFFER_SIZE, offset=0)
    msgs = [msg.decode('utf-8') for msg in msg_bytes.split(b'\x00')]
    msgs = [msg for msg in msgs if len(msg) > 0]
    for idx, msg in enumerate(msgs):
        print(f"{idx+1}: {msg}")


# Helper to receive exactly n bytes from the socket
def recvn(sock: socket.socket, n: int) -> bytes:
    data = b''

    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    
    return data


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: server.py <iface>")
        sys.exit(1)
    
    iface = sys.argv[1]

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
        print(f"Interface {iface} not found.", file=sys.stderr)
        sys.exit(1)

    # Bind a socket to the TCP port 18515, waiting for the client connection
    # Connection parameters exchange must be reliable, hence we must use on a TCP socket
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(('', PORT))
    server_sock.listen(1)
    print(f"Waiting for connection on port {PORT}...")
    conn, addr = server_sock.accept()
    print(f"Connection from {addr[0]}")

    # Client connected!
    # Open a context for the RDMA device
    with pyverbs.device.Context(name=iface) as ctx:
        # Create a protection domain
        with pyverbs.pd.PD(ctx) as pd:
            # Allocate a local memory buffer of BUFFER_SIZE=4096 Bytes
            # Then the Memory Region is registered to be used by ibverbs
            # In pyverbs, the library manages the memory area, in C this needs to be allocated by the user
            mr = pyverbs.mr.MR(pd, BUFFER_SIZE, IBV_ACCESS_LOCAL_WRITE)

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

            # === QUEUE PAIR CONNECTION WITH CLIENT ===
            # First, we can move into the INIT state, we do not need any remote information
            init_attr = pyverbs.qp.QPAttr(
                qp_state=IBV_QPS_INIT,
                port_num=1                                 # Port=1 is the only one we have
            )
            init_attr.pkey_index = 0                       # Set the default partition keys table for this QP
            # The QP access flags, you can set the flag for allowing the remote to perform actions (in bitwise OR):
            # - IBV_ACCESS_REMOTE_WRITE - Allow incoming RDMA Writes on this QP
            # - IBV_ACCESS_REMOTE_READ - Allow incoming RDMA Reads on this QP
            # - IBV_ACCESS_REMOTE_ATOMIC - Allow incoming Atomic operations on this QP (not supported in SoftROCE)
            init_attr.qp_access_flags = 0
            
            # Move the QP in the INIT state
            qp.modify(
                init_attr, 
                # This specifies which fields are modifying on the QP
                IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS
            )

            # For moving into the RTR state, we need the information from the remote end
            # Get the local GID for ROCEv2 
            # We query port=1, GID=1. We only have one port, GID=1 since it's the RoCEv2 (GID=0 is the IB one)
            local_gid = ctx.query_gid(port_num=1, index=1)
            # Convert GID in bytes for serialization
            byte_gid = ipaddress.ip_address(local_gid.gid).packed
            
            # TODO 1: Let's prepare our connection data into the struct
            # TODO 1: Exchange: server sends its info first, then receives the client's info

            # TODO 1: Now receive
            # TODO 1: Deserialize

            # We received remote information, and we sent them (so the remote can proceed in RTR too)
            # Let's prepare the QP attributes to move in RTR
            rtr_attr = pyverbs.qp.QPAttr(
                qp_state=IBV_QPS_RTR,
                path_mtu=IBV_MTU_1024             # Path MTU (the maximum payload on the path)
            )
            rtr_attr.dest_qp_num = remote_con.qp_num            # Destination QP (received from the server)
            rtr_attr.rq_psn = 0                                 # Remote PSN (starts from 0, or can be randomly generated)
            rtr_attr.max_dest_rd_atomic = 1                     # Num of RDMA Reads (and atomic operations) outstanding at any time that can be handled by this QP as a receiver
            rtr_attr.min_rnr_timer = 31                         # Maximum NAK Timer, 31 is 491.52 milliseconds

            # GlobalRoute requires the GID in string format, we have the bytes coming from remote
            # ".exploded" prints the full IPv6
            remote_gid_str = ipaddress.ip_address(bytes(remote_con.gid)).exploded
            # Path information to the remote QP
            gr = pyverbs.addr.GlobalRoute(
                dgid=pyverbs.addr.GID(val=remote_gid_str),      # The remote address (in ROCEv2, it is an IP address)
                sgid_index=1                                    # Specify the source GUID index, in this case is 1 since we're using the RoCEv2
            )
            ah_attr = pyverbs.addr.AHAttr(
                gr=gr, 
                is_global=1,                    # The address we are using is not a LID
                port_num=1                      # Port to use to reach the remote, always 1 in this case
            )
            rtr_attr.ah_attr = ah_attr              

            # Move the QP in the RTR state
            qp.modify(
                rtr_attr, 
                IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN | IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER | IBV_QP_AV
            )

            # We are in RTR! We need to move to RTS to complete the QP initialization
            rts_attr = pyverbs.qp.QPAttr(
                qp_state=IBV_QPS_RTS
            )
            rts_attr.sq_psn = 0              # Local Starting PSN (starts from 0, or can be randomly generated)
            rts_attr.timeout = 14            # Min timeout that a QP waits for ACK/NACK from remote QP before retransmitting the packet, 14 is 0.0671 sec
            rts_attr.retry_cnt = 7           # Number of times that QP will try to resend the packets before reporting an error
            rts_attr.rnr_retry = 7           # Number of times that QP will try to resend the packets when an RNR NACK was sent by the remote QP before reporting an error, 7 is infinite
            rts_attr.max_rd_atomic = 1       # Num of RDMA Reads (and atomic operations) outstanding at any time that can be handled by this QP as a sender

            # Move the QP in the RTS state
            qp.modify(
                rts_attr, 
                IBV_QP_STATE | IBV_QP_TIMEOUT | IBV_QP_RETRY_CNT | IBV_QP_RNR_RETRY | IBV_QP_SQ_PSN | IBV_QP_MAX_QP_RD_ATOMIC
            )

            # We made it! We established the connection with the client!
            print("Connection established")

            # Start a thread for polling the CQ
            global keep_polling
            keep_polling = True
            cq_thread = threading.Thread(target=poll_cq, args=(cq, mr))
            cq_thread.start()

            # TODO 2: Post the RECV on the Receive Queue
            # TODO 2: Prepare the scatter/gather elements data structure
            
            # TODO 2: Prepare the WR to post on the Receive Queue
            
            # TODO 2: Post the Work Request on the Receive Queue!

            try:
                cq_thread.join()
            except KeyboardInterrupt:
                # Signal the polling thread to exit
                keep_polling = False

    # Cleanup TCP connection
    conn.close()
    server_sock.close()


if __name__ == '__main__':
    main()
