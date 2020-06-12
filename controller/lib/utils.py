

def printable_bytes(binstr):
    if isinstance(binstr, str):
        return binstr
    binbytes = bytes(binstr)
    printable = bytes([x if (32 <= x < 127) else 46 for x in binbytes])
    return printable.decode('ascii')


def get_hex_str(binstr):
    hexstr = ''
    for i in range(len(binstr)):
        hexstr += '{:02X} '.format(binstr[i])
    return hexstr

