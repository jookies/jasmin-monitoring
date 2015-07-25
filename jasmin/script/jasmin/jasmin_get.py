#!/usr/bin/python

import json, struct, time, argparse, re
from telnetlib import Telnet, IAC, DO, DONT, WILL, WONT, SB, SE, TTYPE

parser = argparse.ArgumentParser(description='Zabbix Jasmin status script')
parser.add_argument('--hostname', required=True, help = "Jasmin's hostname")
parser.add_argument('-m', required=True, help = "Metric to get")
parser.add_argument('-o1', help = "Metric option 1")
parser.add_argument('-o2', help = "Metric option 2")
args = parser.parse_args()

# Configuration
jcli = {'host': '127.0.0.1',
        'port': 8990,
        'username': 'jcliadmin',
        'password': 'jclipwd'}

class jCliSessionError(Exception):
    pass

class jCliKeyError(Exception):
    pass

def process_option(tsocket, command, option):
    if command == DO and option == TTYPE:
        tsocket.sendall(IAC + WILL + TTYPE)
        print 'Sending terminal type "mypython"'
        tsocket.sendall(IAC + SB + TTYPE + '\0' + 'mypython' + IAC + SE)
    elif command in (DO, DONT):
        print 'Will not', ord(option)
        tsocket.sendall(IAC + WONT + option)
    elif command in (WILL, WONT):
        print 'Do not', ord(option)
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
    outcome = None
    try:
        # Connect and authenticate
        tn = Telnet(jcli['host'], jcli['port'])
        tn.set_option_negotiation_callback(process_option)
        tn.set_debuglevel(1000)
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

        # Build outcome for requested metric (args.m)
        if args.m == 'version':
            outcome = version
        elif args.m == 'smpps':
            response = wait_for_prompt(tn, command = "stats --smppsapi\n")
            outcome = get_stats_value(response, args.o1)
        elif args.m == 'httpapi':
            response = wait_for_prompt(tn, command = "stats --httpapi\n")
            outcome = get_stats_value(response, args.o1)
    except Exception, e:
        print 'Error: %s' % e
    finally:
        if tn is not None and tn.get_socket():
            tn.close()

        if outcome is not None:
            print outcome
        else:
            print 'No outcome !'

if __name__ == '__main__':
    main()