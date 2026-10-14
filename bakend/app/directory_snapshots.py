"""Bounded, short-lived direct-child indexes. Never recurse or cache permissions."""
from collections import OrderedDict
from threading import RLock
import time
from uuid import uuid4

class DirectorySnapshots:
    def __init__(self,max_entries=200000,ttl=600,clock=time.monotonic):
        self.limit,self.ttl,self.clock=max_entries,ttl,clock
        self.items=OrderedDict();self.lock=RLock()

    def create(self,context,entries):
        rows=[]
        for row in entries:
            rows.append(row)
            if len(rows)>self.limit:raise ValueError('单目录条目过多，请使用文件名筛选缩小范围')
        rows.sort();identity=uuid4().hex
        with self.lock:
            while self.items and (len(self.items)>=32 or sum(len(v[2]) for v in self.items.values())+len(rows)>self.limit):self.items.popitem(last=False)
            self.items[identity]=(self.clock(),context,rows)
        return identity,rows

    def acquire(self, context, entries):
        """Presentation changes share an index; filesystem stamps remain in context."""
        with self.lock:
            now = self.clock()
            for identity, item in list(self.items.items()):
                if now - item[0] > self.ttl:
                    self.items.pop(identity, None)
                elif item[1] == context:
                    self.items.move_to_end(identity)
                    return identity, item[2], True
        identity, rows = self.create(context, entries)
        return identity, rows, False

    def get(self,identity,context):
        with self.lock:
            item=self.items.get(identity)
            if not item or item[1]!=context or self.clock()-item[0]>self.ttl:
                self.items.pop(identity,None)
                raise ValueError('目录快照已变化或过期，请刷新；已选择的文件不会改变')
            self.items.move_to_end(identity)
            # A busy model must not keep an old index alive indefinitely.
            return item[2]

snapshots=DirectorySnapshots()
