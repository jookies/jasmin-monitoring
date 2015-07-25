#!/usr/bin/python

import json, struct, time, argparse, re, socket
from telnetlib import Telnet, IAC, DO, DONT, WILL, WONT, SB, SE, TTYPE

parser = argparse.ArgumentParser(description='Zabbix Jasmin status script')
args = parser.parse_args()

# Configuration
zabbix_host = 'monitoring.jookies.net'  # Zabbix Server IP
zabbix_port = 30551                     # Zabbix Server Port
jcli = {'host': '127.0.0.1', # Must be the same configured in Zabbix hosts !
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
    'created_at',
    'last_received_elink_at',
    'elink_count',
    'throttling_error_count',
    'submit_sm_count',
    'connected_count',
    'connect_count',
    'bound_trx_count',
    'data_sm_count',
    'submit_sm_request_count',
    'deliver_sm_count',
    'last_sent_pdu_at',
    'unbind_count',
    'last_received_pdu_at',
    'bind_tx_count',
    ]})
keys.append({'httpapi': [
    'server_error_count',
    'last_request_at',
    'throughput_error_count',
    'success_count',
    'route_error_count',
    'request_count',
    'auth_error_count',
    'created_at',
    'last_success_at',
    'charging_error_count',
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

def wait_for_prompt(tn, command = None, prompt = r'jcli :', to = 2):
    """Will send 'command' (if set) and wait for prompt

    Will raise an exception if 'prompt' is not obtained after 'to' seconds
    """

    if command is not None:
        tn.write(command)

    idx, obj, response = tn.expect([prompt], to)
    if idx == -1:
        raise jCliSessionError('Did not get prompt (%s)' % prompt)
    else:
        return response

def get_stats_value(response, key):
    "Parse response and get key's value, otherwise raise a jCliKeyError"
    p = r"#%s\s+([0-9A-Za-z -:]+)" % key
    m = re.search(p, response, re.MULTILINE)
    if not m:
        raise jCliKeyError('Key (%s) not found !' % key)
    else:
        return m.group(1)

def main():
    tn = None
    try:
        # Connect and authenticate
        tn = Telnet(jcli['host'], jcli['port'])
        tn.set_option_negotiation_callback(process_option)
        
        # for telnet session debug:
        #tn.set_debuglevel(1000)
        
        tn.read_until('Authentication required', 1)
        tn.write("\n")
        tn.read_until("Username:", 1)
        tn.write(jcli['username']+"\n")
        tn.read_until("Password:", 1)
        tn.write(jcli['password']+"\n")

        # We must be connected
        idx, obj, response = tn.expect([r'Welcome to Jasmin (\d+\.\d+[a-z]+\d+) console'], 2)
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

        # Send packet to zabbix
        send_to_zabbix(metrics, zabbix_host, zabbix_port)
    except Exception, e:
        print 'Error: %s' % e
    finally:
        if tn is not None and tn.get_socket():
            tn.close()

if __name__ == '__main__':
    main()