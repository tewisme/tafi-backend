import os

path = 'D:/Tafi/tiktok/tools/backend_server/admin.py'
with open(path, 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('\ndef reset_hwid():', '\ndef reset_hwid():') 
# Wait, if it's literally backslash-n in the file, we can replace '\\ndef' with '\\n\\ndef'
