"""Drain bounded unread request bodies before closing TLS connections.

cheroot drains KnownLengthRFile only on keep-alive connections. Windows may
reset a TLS connection closed with unread input, losing an otherwise valid
response (notably an asynchronous POST that does not use its JSON body).
"""
from cheroot.server import HTTPRequest,HTTPConnection

class CompleteRequest(HTTPRequest):
 def send_headers(self):
  if self.status[:3]!=b'413' and self.ready:
   remaining=getattr(self.rfile,'remaining',0)
   while remaining>0:
    chunk=self.rfile.read(min(65536,remaining))
    if not chunk:break
    remaining=getattr(self.rfile,'remaining',0)
   if self.chunked_read:
    while self.rfile.read(65536):pass
  return super().send_headers()

class CompleteConnection(HTTPConnection):
 RequestHandlerClass=CompleteRequest
