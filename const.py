DEF_BLOCK_SIZE = 512    # RFC 1350
MIN_BLOCK_SIZE = 8      # RFC 2348
MAX_BLOCK_SIZE = 65464  # RFC 2348

DEF_WINDOW_SIZE = 1
MIN_WINDOW_SIZE = 1
MAX_WINDOW_SIZE = 65535

DEF_TIMEOUT = 1
MAX_RETRY = 3           # 最大重传次数

# TFTP opcode
RRQ     = b'\x00\x01'
WRQ     = b'\x00\x02'
DATA    = b'\x00\x03'
ACK     = b'\x00\x04'
ERROR   = b'\x00\x05'
OACK    = b'\x00\x06'    # RFC 2347