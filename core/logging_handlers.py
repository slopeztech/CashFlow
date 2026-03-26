import time
from logging.handlers import TimedRotatingFileHandler


class WindowsSafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Skips a rollover cycle if Windows cannot rename a locked file."""

    def doRollover(self):
        try:
            super().doRollover()
            return
        except PermissionError:
            # On Windows, external readers (editor/tail/AV) can lock the file.
            if self.stream:
                self.stream.close()
                self.stream = None

            current_time = int(time.time())
            next_rollover = self.computeRollover(current_time)
            while next_rollover <= current_time:
                next_rollover += self.interval
            self.rolloverAt = next_rollover

            if not self.delay:
                self.stream = self._open()
