#!/usr/bin/python
# This a script is called by Zabbix agent to discover rabbitmq queues

import json, struct, time, argparse, re, socket, sys
from lockfile import FileLock, LockTimeout, AlreadyLocked
from pyrabbit.api import Client as RabbitClient

# The script must not be executed simultaneously
lock = FileLock("/tmp/rabbiqmq_discover")

parser = argparse.ArgumentParser(description='Zabbix RabbitMQ LLD script')
parser.add_argument('--hostname', required=True, help = "RabbitMQ's hostname (same configured in Zabbix hosts)")
parser.add_argument('-d', required=True, help = "queues")
args = parser.parse_args()

# Configuration
zabbix_host = 'monitoring.jookies.net'  # Zabbix Server IP
zabbix_port = 30551                     # Zabbix Server Port
rabbitmq = {'host': args.hostname, # Must be the same configured in Zabbix hosts !
            'port': 15672,
            'username': 'guest',
            'password': 'guest',
            'vhost': '/'}

# Discovery keys
keys = []
keys.append('queues')

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
        queues = rabbit.get_queues(rabbitmq['vhost'])
        sys.stdout = oldstdout # enable output

        # Build outcome
        if args.d == 'queues':
            outcome = {'data': []}
            for queue in queues:
                outcome['data'].append({'{#QUEUE}': queue['name']})
    except LockTimeout:
        print 'Lock not acquired, exiting'
    except AlreadyLocked:
        print 'Already locked, exiting'
    except Exception, e:
        print type(e)
        print 'Error: %s' % e
    finally:
        if outcome is not None:
            print json.dumps(outcome)

        # Release the lock
        if lock.i_am_locking():
            lock.release()

if __name__ == '__main__':
    main()