import os
import time
import socket
import logging

from const import *


class TftpClient:
    """ TFTP客户端 """
    def __init__(self, server, port=69, tftp_dir='.'):
        self.req_addr = (server, port)      # 请求地址
        self.trans_addr = None              # 传输地址
        self.tftp_dir = os.path.abspath(tftp_dir)

        self.blksize = DEF_BLOCK_SIZE
        self.windowsize = DEF_WINDOW_SIZE

        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(('0.0.0.0', 0))
        self.s.settimeout(DEF_TIMEOUT)
        self.s.connect((server, 0))
        self.socket_buf_size = self.s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

    def send(self, opcode, **kwargs):
        pkt = opcode
        if opcode == ERROR:
            errcode = kwargs['errcode']
            pkt += errcode.to_bytes(2, 'big')
            if errcode == 0 and 'errmsg' in kwargs:
                pkt += kwargs['errmsg'].encode() + b'\x00'
        elif opcode == ACK:
            pkt += kwargs['block'].to_bytes(2, 'big')
        elif opcode == OACK:
            pkt += kwargs['option']
        elif opcode == DATA:
            pkt += kwargs['block'].to_bytes(2, 'big') + kwargs['data']
        elif opcode == RRQ:
            option = ''
            if 'blksize' in kwargs:
                self.send_blksize = kwargs['blksize']
                option += f'blksize\x00{kwargs['blksize']}\x00'
            if 'windowsize' in kwargs:
                self.send_windowsize = kwargs['windowsize']
                option += f'windowsize\x00{kwargs['windowsize']}\x00'
            if 'tsize' in kwargs:
                option += f'tsize\x00{kwargs['tsize']}\x00'
            if 'timeout' in kwargs:
                self.send_timeout = kwargs['timeout']
                option += f'timeout\x00{kwargs['timeout']}\x00'
            pkt += f'{kwargs['file_name']}\x00octet\x00{option}'.encode()
        else:
            return
        if opcode in [RRQ, WRQ]:
            self.s.sendto(pkt, self.req_addr)
        else:
            self.s.sendto(pkt, self.trans_addr)

    def recv(self):
        data, self.trans_addr = self.s.recvfrom(65536)
        opcode = data[:2]
        if opcode == OACK:
            options = data[2:-1].lower().decode().split('\x00')
            if 'blksize' in options:
                idx = options.index('blksize')
                blksize = int(options[idx + 1])
                if blksize > self.send_blksize:
                    self.send(ERROR, errcode=0, errmsg='invalid option')
                    raise Exception(f'服务器应答blksize={blksize}大于请求值{self.send_blksize}')
                self.blksize = blksize
            if 'windowsize' in options:
                idx = options.index('windowsize')
                windowsize = int(options[idx + 1])
                if windowsize > self.send_windowsize:
                    self.send(ERROR, errcode=0, errmsg='invalid option')
                    raise Exception(f'服务器应答windowsize={windowsize}大于请求值{self.send_windowsize}')
                self.windowsize = windowsize
            if 'tsize' in options:
                idx = options.index('tsize')
                tsize = int(options[idx + 1])
            self.send(ACK, block=0)
            return self.recv()
        elif opcode == ERROR:
            errcode = int.from_bytes(data[2:4], 'big')
            raise Exception(f'传输失败，错误码：{errcode}')
        elif opcode == DATA:
            block = int.from_bytes(data[2:4], 'big')
            return (block, data[4:])
        else:
            return self.recv()

    def download(self, remote_file, local_file, **options):
        self.send(RRQ, file_name=remote_file, **options)
        file_path = os.path.join(self.tftp_dir, local_file)
        f = open(file_path, 'wb')
        last_block = 0
        nack_block = -1
        retry = 0
        finish = False
        while not finish:
            i = 0
            while i < self.windowsize:  # 开始为1，若请求有windowsize，在第一次recv后，此值会更新
                i += 1
                try:
                    cur_block, data = self.recv()
                    retry = 0
                except TimeoutError:
                    print(f'块：{(last_block + 1) & 0xffff}，超时')
                    if retry == MAX_RETRY:
                        raise Exception(f'超过最大重试次数{MAX_RETRY}')
                    cur_block = last_block
                    retry += 1
                    break
                if cur_block != (last_block + 1) & 0xffff:
                    print(f'recv:{cur_block} exp:{last_block+1}')
                    # 接收块序号小于已经接收的块，忽略掉
                    if (cur_block - last_block) & 0xffff > 0x8888:
                        continue
                    # 相同nack只发送一次
                    if nack_block != last_block:
                        cur_block = nack_block = last_block
                    break
                last_block = cur_block
                f.write(data)
                if len(data) < self.blksize:
                    finish = True
                    break
            if cur_block == last_block:
                self.send(ACK, block=cur_block)


if __name__ == '__main__':
    client = TftpClient('127.0.0.1')
    t0 = time.time()
    client.download('10m', 'down', blksize=65535, windowsize=65535, tsize=0)
    cost = time.time() - t0
    print(f'下载完成，耗时：{cost:.3f}s')
    # client.download('uboot.bin', blksize=MAX_BLOCK_SIZE, windowsize=65535)