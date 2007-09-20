#!/usr/bin/python

import EpicsCA
from SimpleDB import SimpleDB, SimpleTable
from Master import ArchiveMaster
from Cache import Cache
from config import dbuser,dbpass,dbhost,dblogdir,masterdb
from util import normalize_pvname, get_force_update_time, escape_string

import time
import sys
import os
import getopt

MAX_EPOCH = 2**31


class Archiver:
    MIN_TIME = 1000000
    def __init__(self,dbname=None,**args):
        self.dbname = dbname
        self.dbuser = dbuser
        self.dbpass = dbpass
        self.dbhost = dbhost
        self.debug  = 0
        self.force_checktime = 0
        self.messenger = sys.stdout
        self.master  = None
        for k,v in args.items():
            if   (k == 'debug'):      self.debug     = v
            elif (k == 'user'):       self.dbuser    = v
            elif (k == 'passwd'):     self.dbpass    = v
            elif (k == 'messenger'):  self.messenger = v
            elif (k == 'host'):       self.dbhost    = v
            elif (k == 'master'):     self.master    = v

        if self.master is None: self.master = ArchiveMaster()
        if self.dbname is None: self.dbname = self.master.get_currentDB()
        self.cache  = Cache()
        self.db = SimpleDB(db=self.dbname,
                           user=self.dbuser,
                           passwd=self.dbpass,
                           host=self.dbhost,
                           messenger=self.messenger,
                           debug=self.debug)
        
    def sync_with_cache(self):
        self.pvinfo = {}
        self.last_insert = {}
        db_pvs = self.db.tables['pv'].select()

        self.cache_names = self.cache.get_pvlist()
        for pv in db_pvs:  self.initialize_data(pv)
        self.check_for_new_pvs()

    def check_for_new_pvs(self):
        self.cache_names = self.cache.get_pvlist()        
        # print 'check for new pvs ', len(self.cache_names), len(self.pvinfo)
        for p in self.cache_names:
            if not self.pvinfo.has_key(p):
                self.write('adding %s to Archive\n' % p)
                self.add_pv(p)
   
    def db_for_time(self,t):
        return self.master.db_for_time(t)

    def get_data(self,pvname,t0,t1):
        "get data from database"
        if not self.pvinfo.has_key(pvname): return []
        
        db0 = self.master.db_for_time(t0)
        db1 = self.master.db_for_time(t1)
        dat = []
        table = self.pvinfo[pvname]['data_table']
        pvid  = self.pvinfo[pvname]['id']
        for db in (db0,db1):
            self.db.use(db)
            self.db.execute('select time,value from %s where pv_id=%i and time>=%f and time<=%f order by time' %                       (table,pvid,t0,t1))
            for i in self.db.fetchall():
                dat.append((i['time'],i['value']))
        return dat

    def write(self,s):
        self.messenger.write(s)
        
    def drop_pv(self,name):
        self.db.execute("delete from pv where pv_name=%s" % name)
        
    def add_pv(self,name,description=None,graph={},deadtime=None,deadband=None):
        """add PV to the database"""
        pvname = normalize_pvname(name)
        if self.pvinfo.has_key(pvname):
            self.write("PV %s is already in database.\n" % pvname)
            return None

        # create an Epics PV, check that it's valid
        try:
            pv = EpicsCA.PV(pvname,connect=True)
            typ = pv.type
            count = pv.count
        except:
            typ= 'int'
            count = 1

        # determine type
        dtype = 'string'
        if (typ in ('int','long','short')): dtype = 'int'
        if (typ in ('enum',)):              dtype = 'enum'
        if (typ in ('double','float')):     dtype = 'double'
        
        # determine data table
        table = "dat%3.3i" % ((hash(pvname) % 128) + 1)
        
        # determine descrption (don't try too hard!)
        if (description == None):
            if pvname.endswith('.VAL'):
                descpv  = pvname + '.DESC'
            try:
                dp = EpicsCA.PV(descpv,connect=True)
                description = dp.char_value
                dp.disconnect()
            except:
                pass
        if description is None: description = ''

        # device type
        devtype = None
        idot = pvname.find('.')
        if idot >0:
            try:
                dpv = EpicsCA.PV(pvname[:idot] + '.DTYP',connect=True)
                devtype = dpv.char_value
                dpv.disconnect()
            except:
                pass            
        if devtype     is None: devtype = ''

        # set graph default settings
        gr = {'high':'','low':'','type':'normal'}
        gr.update(graph)
        if (dtype == 'enum'):
            gr['type'] = 'discrete'
            gr['low'] = 0
            gr['high'] =  len(pv.enum_strings)
        elif dtype == 'double':
            gr['type'] = 'normal'
            dx = description.lower()
            for i in ('cathode','pirani','pressure'):
                if dx.find(i) >= 0: 
                    gr['type'] = 'log'
        
        if (deadtime == None):
            deadtime = 10.0
            if (dtype == 'enum'):     deadtime =  5.0  # (ENUMS take little space, rarely change)
            if (gr['type'] == 'log'): deadtime = 30.0  # (pressures change very frequently)

        if (deadband == None):
            deadband = 1.e-5
            if dtype in ('enum','string'):     deadband =  0.5
            if (gr['type'] == 'log'): deadband = 1.e-4
            
        self.db.tables['pv'].insert(pv_name    = pvname,
                                    pv_type    = dtype,
                                    description= description,
                                    data_table = table,
                                    deadtime   = deadtime,
                                    deadband   = deadband,
                                    graph_lo   = gr['low'],
                                    graph_hi   = gr['high'],
                                    graph_type = gr['type'])

        r = self.db.tables['pv'].select_where(pv_name=pvname)[0]
        ftime = get_force_update_time()
        self.pvinfo[pvname] = (r['data_table'],r['id'],r['deadtime'],r['deadband'], ftime)
        self.last_insert[name] = [0,None]
        pv.disconnect()
        
    def get_pvlist(self):
        return self.cache.get_pvlist()        

    def reread_db(self,pvname):
        ' re-read database settings for PV'
        if self.pvinfo.has_key(pvname):
            old = self.pvinfo[pvname]
            try:
                r   = self.db.tables['pv'].select_where(pv_name=pvname)[0]
                self.pvinfo[pvname] = (r['data_table'],r['id'],r['deadtime'],r['deadband'], old[4])
            except:
                pass
        else:
            self.add_pv(pvname)
    
    def initialize_data(self,pv):
        nam_x = pv['pv_name']
        pvid  = pv['id']
        table = pv['data_table']
        dtime = pv['deadtime']
        dband = pv['deadband']

        name = normalize_pvname(nam_x)
        if name not in self.cache_names:  self.cache.add_pv(name)

        ftime = get_force_update_time()
        self.pvinfo[name]      = (table,pvid,dtime,dband, ftime)
        self.last_insert[name] = (0,None)

        t0 = int(time.time() - 86400)

        self.db.execute("""select time,value from %s where pv_id=%i and time>%i order by time desc limit 1""" % (table, pvid,t0))
        db_dat = self.db.fetchone()
        try:
            self.last_insert[name] = (db_dat['time'],db_dat['value'])
        except:
            print 'no old data found for ', name, db_dat
            r= self.cache.get_full(name)
            if r['value'] is not None and r['ts'] is not None:
                self.update_value(name,table,pvid,r['ts'],r['value'])

    def update_value(self,name,table,pvid,ts,val):
        if ts is None or ts < self.MIN_TIME: ts = time.time()
        sql  = "insert delayed into %s (pv_id,time,value) values (%i,%i,%s)" % (table,
                                                                                pvid, int(ts),
                                                                                escape_string(val))
        try:
            self.db.execute(sql)
        except TypeError:
            self.write("cannot update %s\n")
        self.last_insert[name] =  (ts,val)
        
    def get_cache_changes(self,dt=30):
        """ get list of name,type,value,cvalue,ts from cache """
        return self.cache.get_recent(dt=dt)
    
    def collect(self):
        newvals, forced = [],[]
        for dat in self.get_cache_changes():
            name  = dat['name']
            val   = dat['value']
            ts    = dat['ts'] or time.time()

            if not self.pvinfo.has_key(name): self.add_pv(name)
            table,pvid,dtime,dband,ftime = self.pvinfo[name]

            last_ts,last_val   = self.last_insert[name]
            if last_ts is None: last_ts = 0
            if (ts-last_ts) > dtime:
                do_save = True
                if dat['type'] in ('double','float'):
                    try:
                        do_save = abs(dband) < ( abs((float(val)-float(last_val))/
                                                     max(float(val),float(last_val),1.e-8)))
                    except:
                        pass
                if do_save:
                    self.update_value(name,table,pvid,ts,val)
                    newvals.append((str(name),str(val),ts))

        if (time.time() - self.force_checktime) >= 600.0:
            # Note: this is Very Important, or too many
            # PV connections will be created on the IOCs

            # now check for stale values
            self.force_checktime = time.time()
            self.write('looking for stale values, checking for new settings...\n')
            print 'looking for stale values, checking for new settings...'
            self.check_for_new_pvs()
            for name,data in self.last_insert.items():
                last_ts,last_val = data
                self.reread_db(name)
                table,pvid,dtime,dband,ftime = self.pvinfo[name]
                tnow = time.time()
                if last_ts is None:  last_ts = 0
                if tnow-last_ts > ftime:
                    r = self.cache.get_full(name)
                    if r['type'] is None and r['value'] is None: # an empty / non-cached PV?
                        try:
                            test_pv = EpicsCA.PV(name,connect=True)
                            if (test_pv is None or not test_pv.connected):
                                self.last_insert[name] = (tnow-ftime+7200.0,None)
                                self.write(" PV not connected: %s\n" % name)
                            else:
                                r['value'] = test_pv.value
                            test_pv.disconnect()
                        except:
                            pass
                    else:
                        self.update_value(name,table,pvid,tnow,r['value'])
                        forced.append((str(name),str(r['value']),tnow))
                    
        return newvals,forced

    def show_changed(self,l,prefix=''):
        for v in l:
            self.write("%s  %.30s = %.30s  / %s\n" % (prefix, v[0]+' '*30,
                                                      v[1]+' '*30, time.ctime(v[2])))
        
    def mainloop(self,verbose=False):
        t0 = time.time()
        self.write( 'connecting to database %s ... \n' % self.dbname)
        self.sync_with_cache()
        
        self.write("done. DB connection took %6.3f sec\n" % (time.time()-t0))
        self.write("connecting to %i Epics PVs ... \n" % ( len(self.pvinfo) ))
        self.write('======   Start monitoring / saving to DB=%s\n' % self.dbname)

        self.master.set_status(status='running')
        mypid = os.getpid()
        self.master.set_pid(pid=mypid)
        self.master.set_runinfo()

        is_collecting = True
        n_changed = 0
        n_forced  = 0
        t_lastlog = 0
        while is_collecting:
            try:
                newvals,forced   = self.collect()
                n_changed = n_changed + len(newvals)
                n_forced  = n_forced  + len(forced)
                EpicsCA.pend_event(0.1)
                time.sleep(0.1)
                if verbose:
                    self.show_changed(newvals,prefix=' ')
                    self.show_changed(forced, prefix='(f) ')
                elif time.time()-t_lastlog>=59.5:
                    self.write("%s: %i new, %i forced entries.\n" % (time.ctime(), n_changed, n_forced))
                    n_changed = 0
                    n_forced  = 0
                    t_lastlog = time.time()

            except KeyboardInterrupt:
                print 'Interrupted by user.'
                return None
            
            status = self.master.get_status()
            if status in ('stopping','offline'):
                self.master.set_status('offline')
                is_collecting = False
            pid = self.master.get_pid()
            if pid != mypid: is_collecting = False

        return None
    
###
def do_collect(master=None,test=False):
    if master is None: master  = ArchiveMaster()
    
    dbname  = master.get_currentDB()
    logfile = open(os.path.join(dblogdir,"%s.log" % dbname),'a',1)

    if test:     logfile = sys.stdout
    a = Archiver(dbname=dbname,master=master,messenger=logfile)
    a.mainloop()

def do_next(master):
    dbname  = master.get_currentDB()
   
    next_db = master.make_nextdb()
    print 'next database = %s ' % next_db
    master.request_stop()
    master.set_pid(0)
    master.set_currentDB(next_db)

    time.sleep(2.0)
    do_collect(master=master)
    
    
def show_usage():
    print """pvarch:   run and interact with pvcaching / mysql process

  pvarch -h        shows this message.
  pvarch status    show archiving status.
  pvarch start     start collecting data.
  pvarch stop      stop collection.
  pvarch next      generate next db, start collecting into it.
  pvarch list      list archive databases.

"""
    sys.exit()

def main():
    opts, args = getopt.getopt(sys.argv[1:], "h", ["help"])

    try:
        cmd = args.pop(0)
    except IndexError:
        cmd = None
    for (k,v) in opts:
        if k in ("-h", "--help"): cmd = None

    if cmd not in ('status','start', 'debug', 'stop', 'next', 'list',
                   'save','add_pv','drop_pv'):
        show_usage()

    m = ArchiveMaster()
    if   cmd == 'status':    m.show_status()
    elif cmd == 'start':     do_collect(m)
    elif cmd == 'debug':     do_collect(m,test=True)    
    elif cmd == 'next':      do_next(m)
    elif cmd == 'stop':      m.request_stop()
    elif cmd == 'list':      m.show_tables()
    elif cmd == 'save':
        if len(args)==0:
            m.save_db()
        else:
            for i in args:   m.save_db(dbname=i)
    elif cmd == 'add_pv':
        for pvname in args:  m.add_pv(pvname)
    elif cmd == 'drop_pv':
        for pvname in args:  m.drop_pv(pvname)
    else:
        print 'wha?'
    
