#!/usr/bin/python
# This a script that send metrics directly to Zabbix server
# All metrics are gathered using Active agent.

import json, struct, time, argparse, re, socket, sys
from lockfile import FileLock, LockTimeout, AlreadyLocked
from telnetlib import Telnet, IAC, DO, DONT, WILL, WONT, SB, SE, TTYPE

# The script must not be executed simultaneously
lock = FileLock("/tmp/jasmin_get")

parser = argparse.ArgumentParser(description='Zabbix Jasmin status script')
parser.add_argument('--hostname', required=True, help = "Jasmin's hostname (same configured in Zabbix hosts)")
args = parser.parse_args()

# Configuration
zabbix_host = 'monitoring.jookies.net'  # Zabbix Server IP
zabbix_port = 30551                     # Zabbix Server Port
jcli = {'host': args.hostname, # Must be the same configured in Zabbix hosts !
        'port': 8990,
        'username': 'jcliadmin',
        'password': 'jclipwd'}

# Monitoring keys
keys = []
keys.append('version')
keys.append({'smppsapi': [
    'disconnect_count',
    'bound_rx_count',
    'bound_tx_count',
    'other_submit_error_count',
    'bind_rx_count',
    'bind_trx_count',
    'elink_count',
    'throttling_error_count',
    'submit_sm_count',
    'connected_count',
    'connect_count',
    'bound_trx_count',
    'data_sm_count',
    'submit_sm_request_count',
    'deliver_sm_count',
    'unbind_count',
    'bind_tx_count',
]})
keys.append({'httpapi': [
    'server_error_count',
    'throughput_error_count',
    'success_count',
    'route_error_count',
    'request_count',
    'auth_error_count',
    'charging_error_count',
]})
keys.append({'users': {
    'smppsapi': [
        'bind_count',
        'submit_sm_count',
        'submit_sm_request_count',
        'unbind_count',
        'data_sm_count',
        'other_submit_error_count',
        'throttling_error_count',
        'bound_tx_count',
        'bound_rx_count',
        'bound_trx_count',
        'elink_count',
        'deliver_sm_count',
    ],
    'httpapi': [
        'connects_count',
        'rate_request_count',
        'submit_sm_request_count',
        'balance_request_count',
    ],
}})
keys.append({'smppcs': [
    'disconnected_count',
    'other_submit_error_count',
    'submit_sm_count',
    'bound_count',
    'elink_count',
    'throttling_error_count',
    'connected_count',
    'deliver_sm_count',
    'data_sm_count',
    'submit_sm_request_count',
]})


class jCliSessionError(Exception):
    pass

class jCliKeyError(Exception):
    pass

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

def wait_for_prompt(tn, command = None, prompt = r'jcli :', to = 12):
    """Will send 'command' (if set) and wait for prompt

    Will raise an exception if 'prompt' is not obtained after 'to' seconds
    """

    if command is not None:
        tn.write(command)

    idx, obj, response = tn.expect([prompt], to)
    if idx == -1:
        if command is None:
            raise jCliSessionError('Did not get prompt (%s)' % prompt)
        else:
            raise jCliSessionError('Did not get prompt (%s) for command (%s)' % (prompt, command))
    else:
        return response

def get_stats_value(response, key, stat_type = None):
    "Parse response and get key's value, otherwise raise a jCliKeyError"
    if stat_type is None:
        p = r"#%s\s+([0-9A-Za-z -:'\{\}_]+)" % key
    else:
        p = r"#%s\s+%s\s+([0-9A-Za-z -:'\{\}_]+)" % (key, stat_type)

    m = re.search(p, response, re.MULTILINE)
    if not m:
        raise jCliKeyError('Key (%s) not found !' % key)
    else:
        return m.group(1)

def get_list_ids(response):
    "Parse response and get list IDs, otherwise raise a jCliKeyError"
    p = r"^#([A-Za-z0-9_-]+)\s+"
    matches = re.findall(p, response, re.MULTILINE)
    ids = []
    if len(matches) == 0:
        raise jCliKeyError('Cannot extract ids from response %s' % response)
    
    for o in matches:
        if o not in ['Connector', 'User']:
            ids.append(o)
    
    return ids

def main():
    tn = None
    try:
        # Ensure there are no paralell runs of this script
        lock.acquire(timeout=5)

        # Connect and authenticate
        tn = Telnet(jcli['host'], jcli['port'])
        tn.set_option_negotiation_callback(process_option)
        
        # for telnet session debug:
        #tn.set_debuglevel(1000)
        
        tn.read_until('Authentication required', 8)
        tn.write("\n")
        tn.read_until("Username:", 5)
        tn.write(jcli['username']+"\n")
        tn.read_until("Password:", 5)
        tn.write(jcli['password']+"\n")

        # We must be connected
        idx, obj, response = tn.expect([r'Welcome to Jasmin (\d+\.\d+[a-z]+\d+) console'], 5)
        if idx == -1:
            raise jCliSessionError('Authentication failure')
        version = obj.group(1)
        
        # Wait for prompt
        wait_for_prompt(tn)

        # Build outcome for requested key
        metrics = []
        for key in keys:
            if key == 'version':
                metrics.append(Metric(jcli['host'], 'jasmin[%s]' % key, version))
            elif type(key) == dict and 'smppsapi' in key:
                response = wait_for_prompt(tn, command = "stats --smppsapi\n")
                for k in key['smppsapi']:
                    metrics.append(Metric(jcli['host'], 'jasmin[smppsapi.%s]' % k, get_stats_value(response, k)))
            elif type(key) == dict and 'httpapi' in key:
                response = wait_for_prompt(tn, command = "stats --httpapi\n")
                for k in key['httpapi']:
                    metrics.append(Metric(jcli['host'], 'jasmin[httpapi.%s]' % k, get_stats_value(response, k)))
            elif type(key) == dict and 'smppcs' in key:
                response = wait_for_prompt(tn, command = "stats --smppcs\n")
                smppcs = get_list_ids(response)
                for cid in smppcs:
                    response = wait_for_prompt(tn, command = "stats --smppc %s\n" % cid)
                    for k in key['smppcs']:
                        metrics.append(Metric(jcli['host'], 'jasmin[smppc.%s,%s]' % (k, cid), get_stats_value(response, k)))
            elif type(key) == dict and 'users' in key:
                response = wait_for_prompt(tn, command = "stats --users\n")
                users = get_list_ids(response)
                for uid in users:
                    response = wait_for_prompt(tn, command = "stats --user %s\n" % uid)
                    for k in key['users']['httpapi']:
                        metrics.append(Metric(jcli['host'], 'jasmin[user.httpapi.%s,%s]' % (k, uid), get_stats_value(response, k, stat_type = 'HTTP Api')))
                    for k in key['users']['smppsapi']:
                        if k in ['bound_rx_count', 'bound_tx_count', 'bound_trx_count']:
                            r = get_stats_value(response, key = 'bound_connections_count', stat_type = 'SMPP Server')
                            r = json.loads(r.replace("'", '"'))
                            if k == 'bound_rx_count':
                                v = r['bind_receiver']
                            elif k == 'bound_tx_count':
                                v = r['bind_transmitter']
                            elif k == 'bound_trx_count':
                                v = r['bind_transceiver']
                        else:
                            v = get_stats_value(response, k, stat_type = 'SMPP Server')
                        metrics.append(Metric(jcli['host'], 'jasmin[user.smppsapi.%s,%s]' % (k, uid), v))

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
        if tn is not None and tn.get_socket():
            tn.close()

        # Release the lock
        if lock.i_am_locking():
            lock.release()

if __name__ == '__main__':
    main()