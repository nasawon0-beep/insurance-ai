"""Process-local deadline/high-water guard; not a hardware anti-rollback clock."""
import threading
import time

class AuthorityClock:
    def __init__(self):
        self._wall=time.time()
        self._mono=time.monotonic()
        self._high=self._wall
        self._invalid=False
        self._lock=threading.Lock()

    def valid_until(self,deadline):
        with self._lock:
            wall=time.time()
            projected=self._wall+max(0,time.monotonic()-self._mono)
            self._high=max(self._high,wall,projected)
            if wall+120<self._high or self._high>=deadline:
                self._invalid=True
            return not self._invalid
