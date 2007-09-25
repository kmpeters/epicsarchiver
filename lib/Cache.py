#!/usr/bin/env python

import SimpleDB
import EpicsCA
import os
import time
import types
import sys
import getopt

from config import dbuser,dbpass,dbhost, cachedb
from util import clean_input, clean_string, normalize_pvname

null_pv_value = {'value':None,'ts':0,'cvalue':None,'type':None}

class Cache:
    """ store and update a cache of PVs to a sqlite db file"""

    _table_names = ("info", "req", "cache")
    def __init__(self,pvfile=None,dbcursor=None, **kw):
        if dbcursor is not None:
            self.cursor = dbcursor
        else:
            self.cursor = SimpleDB.db_connect(dbname=cachedb,autocommit=0)
            
        self.data = {}
        self.pvs  = {}
        self.pid  = os.getpid()
        if pvfile is not None: self.read_pvlist(pvfile)
        self.get_pvlist()        

    def epics_connect(self,pvname):
        p = EpicsCA.PV(pvname,connect=True,connect_time=1.0)
        EpicsCA.pend_io(1.0)
        if not p.connected:  return False
        self.pvs[pvname] = p
        self.set_value(pv=p)
        return True

    def onChanges(self,pv=None):
        if not isinstance(pv,EpicsCA.PV): return 
        self.data[pv.pvname] = (pv.value , pv.char_value, time.time())
        
    def start_group(self):
        self.data   = {}
        self.begin_transaction()

    def end_group(self):
        for nam,val in self.data.items():
            v = [clean_string(str(i)) for i in val]
            v.append(clean_string(nam))
            q = "update cache set value=%s,cvalue=%s,ts=%s where name=%s" % tuple(v)
            self.cursor.execute(q)
        self.commit_transaction()
        return len(self.data)        

    def test_connect(self):
        self.get_pvlist()
        sys.stdout.write('EpicsArchiver.Cache connecting to %i PVs\n' %  len(self.pvlist))
        t0 = time.time()
        
        for i,pvname in enumerate(self.pvlist):  
            try:
                pv = EpicsCA.PV(pvname,connect=True,connect_time=0.25, use_numpy=False)
                self.pvs[pvname] = pv
            except KeyboardInterrupt:
                return
            except:
                sys.stderr.write('connect failed for %s\n' % pvname)
            # print i,  pvname, pv is not None, time.time()-t0
            
    def connect_pvs(self):
        self.get_pvlist()
        npvs = len(self.pvlist)
        n_notify = npvs / 10
        sys.stdout.write("connecting to %i PVs\n" %  npvs)
        # print 'EpicsArchiver.Cache connecting to %i PVs ' %  npvs
        for i,pvname in enumerate(self.pvlist):  
            try:
                pv = EpicsCA.PV(pvname,connect=False)
                self.pvs[pvname] = pv
            except:
                sys.stderr.write('connect failed for %s\n' % pvname)
                
               
        EpicsCA.pend_io(0.25)
        t0 = time.time()
        self.start_group()
        for i,pvname in enumerate(self.pvlist):
            try:
                pv = self.pvs[pvname]
                pv.connect(connect_time=0.25)
                pv.set_callback(self.onChanges)
                self.data[pvname] = (pv.value , pv.char_value, time.time())
            except KeyboardInterrupt:
                self.exit()
            except:
                sys.stderr.write('connect failed for %s\n' % pvname)
            if i % n_notify == 0:
                EpicsCA.pend_io(0.25)
                sys.stdout.write('%.2f ' % (float(i)/npvs))
                sys.stdout.flush()
        self.end_group()
        
    def testloop(self):
        " "
        sys.stdout.write('Cache test loop \n')
        
        t0 = time.time()
        self.set_date()
        self.connect_pvs()

    def mainloop(self):
        " "
        sys.stdout.write('Starting Epics PV Archive Caching: \n')
        
        t0 = time.time()
        self.set_date()
        self.connect_pvs()
        self.set_pid(self.pid)

        sys.stdout.write('\npvs connected in %.1f seconds\nCache Process ID= %i\n' % (time.time()-t0,self.pid))
        sys.stdout.flush()
        ncached = 0
        while True:
            try:
                self.start_group()
                EpicsCA.pend_event(0.05)
                ncachded = ncached + self.end_group()
                self.set_date()
                self.process_requests()
                if self.get_pid() != self.pid:
                    sys.stdout.write('no longer master.  Exiting !!\n')
                    self.exit()
                if (time.time() - t0) >= 30:
                    sys.stdout.write( '%s: %i values cached since last notice\n' % (time.ctime(),ncached))
                    sys.stdout.flush()
                    t0 = time.time()
                    ncached = 0
                    
            except KeyboardInterrupt:
                return

    def exit(self):
        EpicsCA.cleanup()
        for i in self.pvs.values(): i.disconnect()
        EpicsCA.pend_io(1.0)
        sys.exit()
                   
    def begin_transaction(self):   self.cursor.execute('begin')
    def commit_transaction(self):  self.cursor.execute('commit')

    def sql_exec(self,sql,commit=False):
        if commit: self.cursor.execute('begin')
        self.cursor.execute(sql)
        if commit: self.cursor.execute('commit')        

    def sql_exec_fetch(self,sql):
        self.cursor.execute('begin')
        self.cursor.execute(sql)
        try:
            r =self.cursor.fetchall()
        except:
            r = [{}]
        self.cursor.execute('commit')
        return r

    
    def get_pid(self):
        t = self.sql_exec_fetch("select pid from info")
        return t[0]['pid']
    
    def set_pid(self,pid=1):
        self.sql_exec("update info set pid=%i" % pid,commit=True)

    def shutdown(self):
        self.set_pid(0)
        time.sleep(1.0)
        
    def set_date(self):
        self.sql_exec("update info set datetime=%s,ts=%i" % (clean_string(time.ctime()),time.time()),commit=True)
        
    def get_full(self,pv,add=False):
        " return full information for a cached pv"
        npv = normalize_pvname(pv)
        ret = null_pv_value
        if add and (npv not in self.pvlist):
            self.add_pv(npv)
            sys.stdout.write('adding PV.....\n')
            return self.get_full(pv,add=False)
        try:
            r = self.sql_exec_fetch("select value,cvalue,type,ts from cache where name=%s" % clean_string(npv))
            return r[0]
        except:
            return ret

    def get(self,pv,add=False,use_char=True):
        " return cached value of pv"
        ret = self.get_full(pv,add=add)
        if use_char: return ret['cvalue']
        return ret['value']

    def set_value(self,pv=None,**kws):
        # print 'Set Value ', pv, pv.value, pv.char_value, pv.pvname
        v    = [clean_string(i) for i in [pv.value,pv.char_value,time.time(),pv.pvname]]
        qval = "update cache set value=%s,cvalue=%s,ts=%s where name=%s" % tuple(v)
        self.cursor.execute(qval)


    def add_pv(self,pv):
        """request a PV to be included in caching.
        will take effect once a 'process_requests' is executed."""
        npv = normalize_pvname(pv)
        sql = "insert into req(name) values (%s)"
        if npv not in self.pvlist:
            cmd = sql % clean_string(npv)
            # print 'add_pv: ' , cmd
            self.sql_exec(cmd,commit=True)
        
    def drop_pv(self,pv):
        """delete a PV from the cache

        will take effect once a 'process_requests' is executed."""
        npv = normalize_pvname(pv)
        self.get_pvlist()
        if npv in self.pvlist:
            self.sql_exec("delete from cache where name=%s" % clean_string(npv),commit=True)
        self.get_pvlist()        
        
    def get_pvlist(self):
        " return list of pv names currently being cached"
        self.pvlist = [i['name'] for i in self.sql_exec_fetch("select name from cache")]
        return self.pvlist

    def process_requests(self):
        " process requests for new PV's to be cached"
        req   = self.sql_exec_fetch("select name from req")
        if len(req) == 0: return
        self.get_pvlist()
        sys.stdout.write('adding %i new PVs\n' %  len(req))

        self.begin_transaction()
        cmd = 'insert into cache(ts,name,value,cvalue,type) values'
        es = clean_string
        for n, r in enumerate(req):
            nam= r['name']
            if nam not in self.pvlist:
                nam = str(nam)
                valid = self.epics_connect(nam)
                if valid:
                    pv = self.pvs[nam]
                    self.sql_exec("%s (%i,%s,%s,%s,%s)" % (cmd,time.time(),es(pv.pvname),es(pv.value),es(pv.char_value),es(pv.type)))
                    self.pvlist.append(nam)
                    self.pvs[nam].set_callback(self.onChanges)                    
                else:
                    sys.stderr.write('cache/process_req: invalid pv %s\n' % nam)
                
            self.sql_exec("delete from req where name=%s" % (es(nam)))
        self.commit_transaction()
        self.get_pvlist()

    def get_recent(self,dt=60):
        s = "select name,type,value,cvalue,ts from cache where ts> %i order by ts"
        return self.sql_exec_fetch(s % (time.time() - dt) )
        
    def cache_status(self,brief=False,dt=60):
        "shows number of updated PVs in past 60 seconds"
        
        ret = self.get_recent(dt=dt)
        pid = self.get_pid()
        if not brief:
            for  r in ret:
                sys.stdout.write("  %s %.25s = %s\n" % (time.strftime("%H:%M:%S",time.localtime(r['ts'])),
                                                      r['name']+' '*20,r['value']))
            sys.stdout.write('%i PVs had values updated in the past %i seconds. pid=%i\n' % (len(ret),dt,pid))
        return len(ret)

