# Мonkey-patch для замены отсутствующего модуля imghdr в Python 3.13
import sys
import os

class ImghdrModule:
    @staticmethod
    def what(filepath):
        if not os.path.isfile(filepath):
            return None
        try:
            with open(filepath, 'rb') as f:
                head = f.read(32)
        except:
            return None
        
        if len(head) < 32:
            return None
        
        if head.startswith(b'\xff\xd8\xff'):
            return 'jpeg'
        elif head.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'png'
        elif head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
            return 'gif'
        elif head.startswith(b'BM'):
            return 'bmp'
        return 'jpeg'

sys.modules['imghdr'] = ImghdrModule()
