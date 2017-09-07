import logging
import os
import subprocess
import sys
import tempfile
from argparse import ArgumentParser
from contextlib import contextmanager

import argcomplete
import signal

from snactor import loader
from snactor import registry

ACTOR_DIRECTORY = '/usr/share/leapp/actors'
SCHEMA_DIRECTORY = '/usr/share/leapp/schema'
VERSION = "0.2-dev"


def _port_spec(arg):
    """Converts a port forwarding specifier to a (host_port, container_port) tuple

    Specifiers can be either a simple integer, where the host and container port are
    the same, or else a string in the form "host_port:container_port".
    """
    host_port, sep, container_port = arg.partition(":")
    host_port = int(host_port)
    if not sep:
        container_port = host_port
    else:
        container_port = int(container_port)
    return str(host_port), container_port


def _to_port_map(items):
    port_map = []
    for source, target in items:
        port_map.append({
            'protocol': 'tcp',
            'exposed_port': int(target),
            'port': int(source)})
    return {'ports': port_map}


def _path_spec(arg):
    path = os.path.normpath(arg)
    if not os.path.isabs(path):
        raise ValueError("Path '{}' is not absolute or valid.".format(str(arg)))

    return path


def _make_argument_parser():
    ap = ArgumentParser()
    ap.add_argument('-v', '--version', action='version', version=VERSION, help='display version information')
    parser = ap.add_subparsers(help='sub-command', dest='action')
    migrate_cmd = parser.add_parser('migrate-machine', help='migrate source VM to a target container host')

    migrate_cmd.add_argument('machine', help='source machine to migrate')
    migrate_cmd.add_argument('-t', '--target', default='localhost', help='target VM name')
    migrate_cmd.add_argument(
        '--tcp-port',
        default=None,
        dest="forwarded_tcp_ports",
        nargs='*',
        type=_port_spec,
        help='(Re)define target tcp ports to forward to macrocontainer - [target_port:source_port]'
    )
    migrate_cmd.add_argument(
        '--no-tcp-port',
        default=None,
        dest="excluded_tcp_ports",
        nargs='*',
        type=_port_spec,
        help='define tcp ports which will be excluded from the mapped ports [[target_port]:source_port>]'
    )

    migrate_cmd.add_argument(
        '--exclude-path',
        default=None,
        dest="excluded_paths",
        nargs='*',
        type=_path_spec,
        help='define paths which will be excluded from the source'
    )
    migrate_cmd.add_argument("--ignore-default-port-map", default=False,
                             help='Default port mapping detected by leapp toll will be ignored', action="store_true")
    migrate_cmd.add_argument('--container-name', '-n', default=None,
                             help='Name of new container created on target host')
    migrate_cmd.add_argument(
        '--force-create',
        action='store_true',
        help='force creation of new target container, even if one already exists'
    )
    migrate_cmd.add_argument('--disable-start', dest='disable_start', default=False,
                             help='Migrated container will not be started immediately', action="store_true")
    migrate_cmd.add_argument('--target-user', default="root", help='Connect as this user to the target via ssh')
    migrate_cmd.add_argument('--source-user', default="root", help='Connect as this user to the source via ssh')

    return ap


def _make_base_object(s):
    return {"value": s}


def _migrate_machine(arguments):
    default_excluded_paths = ['/dev/*', '/proc/*', '/sys/*', '/tmp/*', '/run/*', '/mnt/*', '/media/*', '/lost+found/*']
    data = {
        "target_host": _make_base_object(arguments.target),
        "source_host": _make_base_object(arguments.machine),
        "tcp_ports_user_mapping": _to_port_map(arguments.forwarded_tcp_ports or ()),
        "excluded_tcp_port_list": {"tcp": map(lambda x: int(x[0]), arguments.excluded_tcp_ports or ())},
        "excluded_paths": {"value": arguments.excluded_paths or default_excluded_paths},
        "start_container": _make_base_object(not arguments.disable_start),
        "target_user_name": _make_base_object(arguments.target_user),
        "source_user_name": _make_base_object(arguments.source_user),
        "force_create": _make_base_object(arguments.force_create),
        "user_container_name": _make_base_object(arguments.container_name or ''),
    }

    actor = registry.get_actor('migrate-machine')
    if not actor:
        logging.error("Could not find migrate-machine actor to complete the process")
        sys.exit(-1)

    if actor.execute(data):
        logging.info("SUCCESS - Migration has been completed")
    else:
        logging.error("Migration failed")
        sys.exit(-1)


@contextmanager
def _stdout_socket():
    directory = tempfile.mkdtemp('', 'LEAPP_STDOUT', None)
    name = os.join(directory, 'leapp_stdout.sock')
    registry.register_environment_variable('LEAPP_ACTOR_STDOUT_SOCK', name)

    env = os.environ.copy()
    env["LEAPP_ACTOR_STDOUT_SOCK"] = name
    p = subprocess.Popen(["actor-stdout", "server"], env=env)

    yield
    if p.poll():
        logging.error("Output tool ended prematurely with %d", p.returncode)
    else:
        os.kill(p.pid, signal.SIG_TERM)
    os.unlink(name)
    os.rmdir(directory)


def main():
    loader.load(ACTOR_DIRECTORY)
    loader.load_schemas(SCHEMA_DIRECTORY)
    loader.validate_actor_types()

    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG, stream=sys.stderr)
    _COMMANDS = {
        'migrate-machine': _migrate_machine,
    }

    ap = _make_argument_parser()
    argcomplete.autocomplete(ap)
    parsed = ap.parse_args()

    with _stdout_socket():
        _COMMANDS[parsed.action](parsed)
