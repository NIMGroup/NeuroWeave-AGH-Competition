"""Bounded console capture retains both initial context and final outcome."""
class HeadTailLog:
    def __init__(self, stream, head_limit=65536, tail_limit=8192):
        self.stream = stream
        self.remaining = head_limit
        self.tail_limit = tail_limit
        self.tail = bytearray()
        self.omitted = 0

    def write(self, block):
        count = min(self.remaining, len(block))
        if count:
            self.stream.write(block[:count])
            self.stream.flush()
            self.remaining -= count
        rest = block[count:]
        if rest:
            self.omitted += len(rest)
            self.tail.extend(rest)
            del self.tail[:-self.tail_limit]

    def finish(self):
        if self.tail:
            omitted = max(0, self.omitted-len(self.tail))
            notice = f'\n[Console capture limited; {omitted} middle bytes omitted; final output follows]\n'
            self.stream.write(notice.encode()+self.tail)
            self.stream.flush()
