"""Inherited Python-process guard for isolated release fixtures only."""
import os
import sys

real_home = os.environ.get('RELEASE_REAL_HOME', '').replace('\\', '/').casefold().rstrip('/')
python_home = sys.base_prefix.replace('\\', '/').casefold().rstrip('/')
owned_sockets = []


def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0]).replace('\\', '/').casefold()
        if real_home and path.startswith(real_home + '/') and not path.startswith(python_home + '/'):
            raise PermissionError('OPERATING_PROFILE_ACCESS_DENIED')
    if event == 'socket.bind':
        owned_sockets.append(args[0])
    if event == 'socket.connect':
        address = args[1]
        allowed = False
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1', 'localhost'):
            for server in owned_sockets:
                try:
                    if server.getsockname()[1] == address[1]:
                        allowed = True
                except OSError:
                    pass
        if not allowed:
            raise PermissionError('NON_FIXTURE_NETWORK_DENIED')

sys.addaudithook(audit)
