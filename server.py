import os
import time
import socket
import select
import logging
from threading import Thread
from const import *


logging.basicConfig(level = logging.DEBUG, format='%(asctime)s - %(message)s')
log = logging.getLogger(__name__)


class TftpSession(Thread):
    """ TFTP 会话 """
    def __init__(self, tftp_dir, client_addr, req_data: bytes):
        super().__init__(name=f'{client_addr}', daemon=True)

        self.tftp_dir = tftp_dir
        self.req_data = req_data

        self.blksize = DEF_BLOCK_SIZE
        self.windowsize = DEF_WINDOW_SIZE
        
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(('0.0.0.0', 0))
        self.s.connect(client_addr)
        self.s.settimeout(DEF_TIMEOUT)

    def run(self):
        t0 = time.time()
        file_path = self.request_parse()
        t1 = time.time()
        self.transfer(file_path)
        t2 = time.time()
        speed = os.path.getsize(file_path) / 1024 / 1024 / (t2 - t0)
        log.info(f'传输完成，握手时间：{t1-t0:.3f}s，传输时间：{t2-t1:.3f}s，速度：{speed:.2f}M/s')

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
        else:
            return
        self.s.send(pkt)

    def recv(self):
        data = self.s.recv(65536)

        opcode = data[:2]
        if opcode == ACK:
            block = int.from_bytes(data[2:4], 'big')
            return block
        elif opcode == ERROR:
            errcode = int.from_bytes(data[2:4], 'big')
            errmsg = data[4:].decode()
            log.error(f'传输失败，错误码：{errcode} {errmsg}')
            raise SystemExit()
        else:
            return self.recv()

    def request_parse(self):
        """ 握手过程 """
        opcode = self.req_data[:2]
        fields = self.req_data[2:-1].lower().decode().split('\x00')
        file_name = fields[0]
        file_path = os.path.join(self.tftp_dir, file_name)
        mode = fields[1]
        options = fields[2:]

        log.info('')
        req = '下载' if opcode == RRQ else '上传'
        log.info(f'{req}请求，文件名：{file_name}，选项：{options}')

        if opcode == WRQ:
            errmsg = '传输失败，暂未支持上传请求'
            self.send(ERROR, errcode=0, errmsg=errmsg)
            log.error(errmsg)
            raise SystemExit()

        if mode != 'octet':
            errmsg = f'传输失败，不支持的传输模式：{mode}'
            self.send(ERROR, errcode=0, errmsg=errmsg)
            log.error(errmsg)
            raise SystemExit()

        if not os.path.exists(file_path):
            self.send(ERROR, errcode=1)
            log.error(f'传输失败，{file_path} 不存在')
            raise SystemExit()
        
        # 选项协商
        nego = ''
        if 'blksize' in options:
            idx = options.index('blksize')
            blksize = int(options[idx + 1])
            self.blksize = max(min(MAX_BLOCK_SIZE, blksize), MIN_BLOCK_SIZE)
            nego += f'blksize\x00{self.blksize}\x00'
        if 'windowsize' in options:
            idx = options.index('windowsize')
            windowsize = int(options[idx + 1])
            self.windowsize = max(min(MAX_WINDOW_SIZE, windowsize), MIN_WINDOW_SIZE)
            nego += f'windowsize\x00{self.windowsize}\x00'
        if 'tsize' in options:
            idx = options.index('tsize')
            tsize = os.path.getsize(file_path)
            nego += f'tsize\x00{tsize}\x00'
        if nego:
            retry = 0
            while True:
                self.send(OACK, option=nego.encode())
                try:
                    self.recv()
                    break
                except TimeoutError as e:
                    if retry == MAX_RETRY:
                        errmsg=f'传输失败，超过最大重传次数{MAX_RETRY}'
                        self.send(ERROR, errcode=0, errmsg=errmsg)
                        log.error(errmsg)
                        raise SystemExit()
                    retry += 1
                    log.error(f'选项协商超时{retry}次，超时时间：{self.s.gettimeout()}s')
            log.info(f'协商选项：{nego.split('\x00')[:-1]}')

        return file_path
    
    def transfer(self, file_path):
        """ 传输过程 """
        f = open(file_path, 'rb')
        ack_block = 0
        retry = 0
        finish = False
        while not finish:
            for i in range(self.windowsize):
                data = f.read(self.blksize)
                send_block = (ack_block + i + 1) & 0xffff   # uint_16
                self.send(DATA, block=send_block, data=data)
                if len(data) < self.blksize:
                    finish = True
                    break
                readable, writable, exceptional = select.select([self.s], [], [], 0)
                if self.s in readable:      # 窗口未发送完收到数据，有丢包
                    break
            try:
                ack_block = self.recv()
                retry = 0
            except TimeoutError as e:
                if retry == MAX_RETRY:
                    errmsg=f'传输失败，超过最大重传次数{MAX_RETRY}'
                    self.send(ERROR, errcode=0, errmsg=errmsg)
                    log.error(errmsg)
                    raise SystemExit()
                retry += 1
                log.error(f'块：{send_block}，ACK超时{retry}次，超时时间：{self.s.gettimeout()}s')

            if ack_block != send_block:
                offset = ((send_block - ack_block) & 0xffff) * self.blksize
                if finish:
                    finish = False
                    offset = offset - self.blksize + len(data)      # 最后一个窗口有丢包时，纠正offset
                f.seek(-offset, os.SEEK_CUR)
                log.error(f'重传块：{(ack_block + 1) & 0xffff}')

class TftpServer(Thread):
    """ TFTP 服务端 """
    def __init__(self, tftp_dir='.', ip='0.0.0.0', port=69):
        super().__init__()
        self.daemon = True

        self.tftp_dir = os.path.abspath(tftp_dir)
        self.server_addr = (ip, port)
        
    def stop(self):
        self.is_running = False

    def run(self):
        self.is_running = True
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(self.server_addr)
        log.info(f'TFTP服务端已启动：{s.getsockname()}，文件目录：{self.tftp_dir}')
        while self.is_running:
            data, addr = s.recvfrom(1024)
            opcode = data[:2]
            if len(data) >= 4 and opcode in [RRQ, WRQ]:
                session = TftpSession(self.tftp_dir, addr, data)
                session.start()
            # else:
            #     log.error(f'客户端：{address}，未知请求')
        s.close()


if __name__ == '__main__':
    server = TftpServer('.')
    server.start()
    server.join()

