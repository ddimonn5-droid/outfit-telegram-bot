# Файл imghdr.py - заменяет системный модуль
import os
import struct

def what(file_path):
    """
    Определяет тип изображения по его содержимому
    """
    if not os.path.isfile(file_path):
        return None
    
    try:
        with open(file_path, 'rb') as f:
            head = f.read(32)
    except:
        return None
    
    if len(head) < 32:
        return None
    
    # Проверка различных форматов изображений
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    elif head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    elif head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
        return 'gif'
    elif head.startswith(b'BM'):
        return 'bmp'
    elif head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'webp'
    
    return 'jpeg'  # По умолчанию возвращаем jpeg