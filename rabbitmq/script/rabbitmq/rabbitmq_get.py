#!/usr/bin/python
# This a script that send metrics directly to Zabbix server
# All metrics are gathered using Active agent.
# Metrics are covering Jasmin and its connectors queue status

import json, struct, time, argparse, re, socket, sys
from lockfile import FileLock, LockTimeout, AlreadyLocked
from pyrabbit.api import Client as RabbitClient

# The script must not be executed simultaneously
lock = FileLock("/tmp/rabbitmq_get")

parser = argparse.ArgumentParser(description='Zabbix RabbitMQ status script')
parser.add_argument('--hostname', required=True, help = "RabbitMQ's hostname (same configured in Zabbix hosts)")
args = parser.parse_args()

# Configuration
zabbix_host = 'monitoring.jookies.net'  # Zabbix Server IP
zabbix_port = 30551                     # Zabbix Server Port
rabbitmq = {'host': args.hostname, # Must be the same configured in Zabbix hosts !
            'port': 15672,
            'username': 'guest',
            'password': 'guest',
            'vhost': '/'}

# Monitoring keys
keys = []
keys.append({'vhost': [
    'recv_oct',
    'send_oct',
    'messages',
    'messages_unacknowledged',
    'messages_ready',
]})
keys.append({'vhost.message_stats': [
    'ack',
    'deliver_get',
    'deliver',
    'get_no_ack',
    'publish',
]})
keys.append({'queues': [
    'messages',
    'messages_unacknowledged',
    'messages_ready',
    'memory',
    'consumers',
]})

class Metric(object):
    def __init__(self, host, key, value, clock=None):
        self.host = host
        self.key = key
        self.value = value
        self.clock = clock

    def __repr__(self):
        result = None
        if self.clock is None:
            result = 'Metric(%r, %r, %r)' % (self.host, self.key, self.value)
        else:
            result = 'Metric(%r, %r, %r, %r)' % (self.host, self.key, self.value, self.clock)
        return result

def send_to_zabbix(metrics, zabbix_host='127.0.0.1', zabbix_port=10051):
    result = None
    j = json.dumps
    metrics_data = []
    for m in metrics:
        clock = m.clock or ('%d' % time.time())
        metrics_data.append(('{"host":%s,"key":%s,"value":%s,"clock":%s}') % (j(m.host), j(m.key), j(m.value), j(clock)))
    json_data = ('{"request":"sender data","data":[%s]}') % (','.join(metrics_data))
    data_len = struct.pack('<Q', len(json_data))
    packet = 'ZBXD\x01'+ data_len + json_data

    # For debug:
    #print(packet)
    #print(':'.join(x.encode('hex') for x in packet))

    try:
        zabbix = socket.socket()
        zabbix.settimeout(120)
        zabbix.connect((zabbix_host, zabbix_port))
        zabbix.sendall(packet)
        resp_hdr = _recv_all(zabbix, 13)
        if not resp_hdr.startswith('ZBXD\x01') or len(resp_hdr) != 13:
            print('Wrong zabbix response')
            result = False
        else:
            resp_body_len = struct.unpack('<Q', resp_hdr[5:])[0]
            resp_body = zabbix.recv(resp_body_len)
            zabbix.close()

            resp = json.loads(resp_body)
            # For debug
            # print(resp)
            if resp.get('response') == 'success':
                result = True
            else:
                print('Got error from Zabbix: %s' % resp)
                result = False
    except Exception, e:
        print('Error while sending data to Zabbix: %s' % e)
        result = False
    finally:
        return result

def _recv_all(sock, count):
    buf = ''
    while len(buf)<count:
        chunk = sock.recv(count-len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf

def process_option(tsocket, command, option):
    if command == DO and option == TTYPE:
        tsocket.sendall(IAC + WILL + TTYPE)
        #print 'Sending terminal type "mypython"'
        tsocket.sendall(IAC + SB + TTYPE + '\0' + 'mypython' + IAC + SE)
    elif command in (DO, DONT):
        #print 'Will not', ord(option)
        tsocket.sendall(IAC + WONT + option)
    elif command in (WILL, WONT):
        #print 'Do not', ord(option)
        tsocket.sendall(IAC + DONT + option)

class NullWriter(object):
    def write(self, arg):
        pass

def main():
    rabbit = None
    try:
        # Ensure there are no paralell runs of this script
        lock.acquire(timeout=5)

        # Connect to Rabbit
        nullwrite = NullWriter()
        oldstdout = sys.stdout
        sys.stdout = nullwrite # disable output
        rabbit = RabbitClient('%s:%s' % (rabbitmq['host'], rabbitmq['port']), 
            rabbitmq['username'], 
            rabbitmq['password'])
        if not rabbit.is_alive():
            raise Exception('Cannot connect to RabbitMQ')
        vhost = rabbit.get_vhost(rabbitmq['vhost'])
        queues = rabbit.get_queues(rabbitmq['vhost'])
        sys.stdout = oldstdout # enable output

        # Build outcome
        metrics = []
        for key in keys:
            if type(key) == dict and 'vhost' in key:
                for subkey in key['vhost']:
                    if subkey in vhost:
                        metrics.append(Metric(rabbitmq['host'], 'rabbitmq.%s.%s' % ('vhost', subkey), vhost[subkey]))
            elif type(key) == dict and 'vhost.message_stats' in key:
                for subkey in key['vhost.message_stats']:
                    if subkey in vhost['message_stats']:
                        metrics.append(Metric(rabbitmq['host'], 'rabbitmq.%s.%s' % ('vhost.message_stats', subkey), 
                            vhost['message_stats'][subkey]))
            elif type(key) == dict and 'queues' in key:
                for queue in queues:
                    for subkey in key['queues']:
                        if subkey in queue:
                            metrics.append(Metric(rabbitmq['host'], 'rabbitmq.%s.%s[%s]' % ('queue', subkey, queue['name']), 
                                queue[subkey]))

        # Send packet to zabbix
        send_to_zabbix(metrics, zabbix_host, zabbix_port)
    except LockTimeout:
        print 'Lock not acquired, exiting'
    except AlreadyLocked:
        print 'Already locked, exiting'
    except Exception, e:
        print type(e)
        print 'Error: %s' % e
    finally:
        # Release the lock
        if lock.i_am_locking():
            lock.release()

if __name__ == '__main__':
    main()
