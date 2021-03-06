#!/usr/bin/env python
from mod_python import apache

from EpicsArchiver import config, WebStatus, PlotViewer, WebHelp, WebInstruments

# add the location of the web template file to the path 
import sys
sys.path.insert(0, config.template_dir)

def plot(req, pv=None, **kw):
    " plot viewer "
    p = PlotViewer(dbconn=getattr(req,'User_dbconn', None))
    req.User_dbconn = p.dbconn
    
    return p.do_plot(pv=pv, **kw)

def show_page(req,page=None,**kw):
    " status pages "
    p = WebStatus(cache=getattr(req, 'User_cache', None), 
                  dbconn=getattr(req, 'User_dbconn', None))
    req.User_cache  = p.cache
    req.User_dbconn = p.dbconn

    # here we import the list of pages for the web templates
    from pages import pagelist, filemap

    if page is None:
        page = pagelist[0]

    p.begin_page(page, pagelist, refresh=30)

    if page in pagelist:
        template = filemap[page]
        if template.startswith('<'):
            method = template[1:-1]
            try:
                getattr(p,method)()
            except:
                pass
        else:
            p.show_pvfile(template)
   
    p.end_page()
    return p.get_buffer()

def help(req,**kw):
    p = WebHelp()
    return p.show(**kw)

#methods for instruments:
def __Inst(req,method='show',**kw):
    try:
        dbconn = req.dbconn
    except AttributeError:
        dbconn = None
        req.dbconn = dbconn

    p = WebInstruments(dbconn=dbconn)
    req.dbconn = p.dbconn
    out = getattr(p,method)(**kw)
    return out

def instrument(req,**kw):           return __Inst(req,'show',**kw)
def show_instrument(req,**kw):      return __Inst(req,'show',**kw)
def add_instrument(req,**kw):       return __Inst(req,'add_instrument',**kw)
def modify_instrument(req,**kw):    return __Inst(req,'modify_instrument',**kw)    
def manage_positions(req,**kw):     return __Inst(req,'manage_positions',**kw)    
def view_position(req,**kw):        return __Inst(req,'view_position',**kw)
def add_station(req,**kw):          return __Inst(req,'add_station',**kw)

# default function:
def index(req):  return show_page(req,page=None)
