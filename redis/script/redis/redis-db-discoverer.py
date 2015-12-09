import sys

dbs = str.split(sys.stdin.readlines()[0])

# Open json data
r = '{"data":['

counter = 0
for db in dbs:
    if counter > 0:
        r+= ','
    r+= '{"{#DBNAME}":"%s"}' % db

# Close json data
r+= ']}'

print r
