import time
import random
import threading

class Authenticator:
    def __init__(self, token_list):
        if not isinstance(token_list, list) or not token_list or any(not isinstance(token, str) or not token for token in token_list):
            raise ValueError('token_list must contain at least one non-empty token')
        self.tokens = token_list
        self.lock = threading.Lock()
        self.index = 0
        self._last_index = None

    def get_token(self):
        with self.lock:
            slot = self.index
            token = self.tokens[slot]
            self._last_index = slot
            self.index = (self.index + 1) % len(self.tokens)
            return token

    def validate_token(self, token):
        time.sleep(random.uniform(0.1, 0.5))
        return random.choice([True, False])

    def refresh_token(self):
        with self.lock:
            time.sleep(0.2)
            slot = self._last_index if self._last_index is not None else self.index
            self.tokens[slot] = f"token_{random.randint(1000, 9999)}"
            return self.tokens[slot]

    def get_valid_token(self):
        with self.lock:
            slot = self.index
            token = self.tokens[slot]
            self._last_index = slot
            self.index = (self.index + 1) % len(self.tokens)
        if not self.validate_token(token):
            # Refresh the slot that was actually validated.  The previous
            # implementation refreshed the next round-robin slot, leaving the
            # invalid credential in circulation and overwriting a valid one.
            with self.lock:
                self.tokens[slot] = f"token_{random.randint(1000, 9999)}"
                token = self.tokens[slot]
        return token
