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
