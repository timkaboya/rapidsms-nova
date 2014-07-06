# encoding: utf-8
# vim: expandtab ts=2

import copy
from datetime import datetime, date, time
from decimal import Decimal
from rapidsmsrw1000.apps.ubuzima.models import *
from ..messages.parser import *
from ....settings import __DEFAULTS, THE_DATABASE as postgres
import psycopg2
import re
from sys import stderr
from StringIO import StringIO

REPORTS_TABLE = __DEFAULTS['REPORTS']

class ThouRow:
  def __init__(self, query, value = None, **kwargs):
    self.query  = query
    self.hooks  = kwargs.get('hooks', {})
    self.kwargs = kwargs
    self.value  = value

  def set_row(self, r):
    self.value  = r

  def __setitem__(self, k, v):
    raise Exception, 'Read-only, man. :-p'

  def __getitem__(self, k):
    try:
      if not self.query.cols:
        self.query.load_description()
        return self[k]
      return self.value[self.query.cols[k]]
    except KeyError:
      hk  = self.hooks[k]
      hkt = type(hk)
      if hkt == type({}):
        return self.query.specialise(hk)
      if callable(hk):
        return hk(self, k)
      return hk

class ThouQuery:
  def __init__(self, djconds, tn, **kwargs):
    self.djconds  = djconds
    self.tablenm  = kwargs.get('table', tn)
    self.kwargs   = kwargs
    self.cursor   = None
    self.names    = kwargs.get('cols', [])
    self.annots   = kwargs.get('annotate', {})
    self.optim    = kwargs.get('optimise', {})
    self.precount = kwargs.get('precount')
    self.flat     = False
    self.sort     = None

  def where(self, k, v):
    self.djconds[k] = v
    return self

  def put_names(self, dat):
    self.names  = dat

  def set_names(self, dat):
    self.names  = dat.keys()
    self.cols   = dat

  def active_columns(self, qid):
    return self.names or ThouReport.active_columns(qid)

  def assemble_sort(self, asc):
    return ThouReport.assemble_sort(asc)

  def assemble_conditions(self, conds):
    return ThouReport.assemble_conditions(conds)

  def specialise(self, kwargs):
    nova          = copy.copy(self)
    nova.djconds  = copy.copy(self.djconds)
    for k in kwargs:
      nova.djconds[k] = kwargs[k]
    return nova

  def filter(self, **kwargs):
    nova          = copy.copy(self)
    nova.djconds  = copy.copy(self.djconds)
    for k in kwargs:
      nova.djconds[k] = ThouReport.alter_condition(kwargs, k)
    return nova

  # TODO: Make sure this works, if it is necessary in our current DB design.
  def distinct(self, *args, **kwargs):
    return self
    raise Exception, str((args, kwargs))
    pass

  # TODO: Use the flatness in the next call.
  # TODO: This doesn’t work yet.
  def values(self, *vals, **kwargs):
    it        = copy.copy(self)
    it.names  = kwargs.get('new', ['orig_%s' % (x,) for x in vals])
    # dem       = it.fetchall()
    # return dem
    # raise Exception, str((it.query, it.cursor.rowcount, it.cursor.query))
    return it

  # TODO.
  def extra(self, **kwargs):
    return self

  def exists(self):
    self.execute()
    return max(self.cursor.rowcount, 0) < 1

  def annotate(self, **kwargs):
    return self.specialise(annotate = kwargs)

  def order_by(self, _, cue = ''):
    if len(cue) < 1: return self
    asc, gd, etc = False, cue[0], cue[1:]
    if gd != '-':
      asc = True
      etc = cue
    ans       = copy.copy(self)
    ans.sort  = (etc, asc)
    return ans

  def hdl_(self, qry):
    nq  = qry.specialise({})
    nq.names  = ['COUNT(*) AS tot']
    nq.kwargs.pop('optims', None)
    try:
      return nq[0].value[0]
    except Exception, e:
      return 0

  def count(self):
    if not self.cursor:
      self.execute()
    if not (self.precount is None):
      return self.precount
    minim = 0
    if self.optim.get('name'):
      hdl = self.optim.get('counter', self.hdl_)
      if self.cursor.rowcount < minim:
        self.precount  = hdl(self)
        return self.count()
    return max(minim, self.cursor.rowcount)

  def __names(self, curz, dft):
   return [x.name for x in curz.description] if curz.description else dft

  def execute(self, retries = True):
    if not self.cursor:
      try:
        self.names, self.cursor = self.__execute()
      except psycopg2.ProgrammingError, e:
        if retries:
          postgres.commit()
          self.migrate(self.kwargs.get('migrations', []), e)
          return self.execute(False)
        raise e
      self.cols               = self.__set_names()
    stderr.write('>>>\t%s\r\n' % (self.query, ))
    return self

  def migrate(self, migs, _):
    curz = postgres.cursor()
    for mig in migs:
      ThouReport.ensure_column(curz, self.tablenm, *mig)
    curz.close()

  def fetchall(self):
    if not self.cursor:
      self.execute()
      return self.cursor.fetchall()
    return self.cursor.fetchall()

  def fetch(self):
    if not self.cursor:
      self.execute()
      return self.fetch()
    return self.fetchone()

  def __getitem__(self, them):
    if not self.cursor:
      self.execute()
      return self[them]
    if type(them) in [type('method'), type(u'method')]:
      raise AttributeError, 'Call the method itself, O foolish and mal-designed Django templates engine!'
      return apply(getattr(self, them))
    if type(them) == type(0):
      try:
        self.cursor.scroll(them, mode = 'absolute')
      except Exception:
        return None
      seul  = self.cursor.fetchone()
      return ThouRow(self, seul, **self.kwargs)
    dem = []
    try:
      self.cursor.scroll(them.start, mode = 'absolute')
      curz  = self.cursor.fetchmany(them.stop - them.start)
      for ent in curz:
        dem.append(ThouRow(self, ent, **self.kwargs))
    except Exception:
      pass
    return dem

  def old__getitem__(self, them):
    if type(them) != slice:
      raise ValueError, ('Should be a slice [column-name:row], not a %s (%s)' % (type(them), str(them)))
    try:
      return them.stop[self.names[them.start]]
    except ValueError:
      raise NameError, ('No column called "%s" (has: %s).' % (colnm, ', '.join(cols)))

  def load_description(self):
    ans = {}
    nms = []
    pos = 0
    if not self.cursor:
      self.__execute()
      return self.load_description()
    if self.cursor.description:
      for dsc in self.cursor.description:
        nms.append(dsc.name)
        ans[dsc.name] = pos
        pos           = pos + 1
    else:
      self.count()
      return self.load_description()
    self.names  = nms
    self.cols   = ans
    return (nms, ans)

  def __set_names(self):
    self.cols   = {}
    notI        = 0
    for x in (self.names or {}):
      self.cols[x]  = notI
      notI          = notI + 1
    return self.cols

  # TODO: Work with views to ensure closing of cursors.
  def __execute(self):
    qry   = self.query
    curz  = None
    optim = self.optim
    if optim:
      curn  = optim['name']
      curz  = postgres.cursor(curn)
    else:
      curz  = postgres.cursor()
    curz.execute(qry)
    cols  = self.__names(curz, optim.get('cols', self.kwargs.get('cols')))
    return (cols, curz)

  def close(self):
    if not self.cursor: return
    return self.cursor.close()

  def __enter__(self):
    pass  # For now.

  def __exit__(self, *args):
    self.close()

  @property
  def query(self):
    qry   = u' FROM %s%s%s' % (self.tablenm, self.assemble_conditions(self.djconds), self.assemble_sort(self.sort))
    cols  = ', '.join(self.kwargs.get('cols', self.active_columns(self.kwargs.get('qid')) or ['*']))
    annot = ['(SELECT %s%s) AS %s' % (self.annots[k], qry, k) for k in self.annots]
    annot.append('%s%s' % (cols, qry))
    qry = ', '.join(annot)
    return ' '.join(['SELECT', qry])

  def __unicode__(self):
    return self.query

class ThouReportBatch:
  def __init__(self):
    self.tables   = {}

  def append(self, tn, cols, vals, sepstr = '\t', nullstr = '\\N'):
    if not (tn in self.tables):
      self.tables[tn] = (cols, StringIO())
      return self.append(tn, cols, vals)
    _, sio  = self.tables[tn]
    sio.write(sepstr.join(vals))
    sio.write('\n')
    return self

  def store(self):
    curz  = postgres.cursor()
    for tbl in self.tables:
      cols, sio = self.tables[tbl]
      # TODO.
      sio.seek(0)
      # stderr.write('>>>\t%s:%s:%s\r\n' % (tbl, '.'.join(cols), sio.read()))
      sio.seek(0)
      curz.copy_from(sio, tbl, columns = cols)
    curz.close()
    postgres.commit()
    return self
    
  def run(self):
    return self.store()

class ThouReport:
  'The base class for all "RapidSMS 1000 Days" reports.'
  created   = False
  columned  = False

  def __init__(self, msg):
    'Initialised with the Message object to which it is coupled.'
    self.msg  = msg

  def __insertables(self, fds):
    '''Returns a hash of all the columns that will be affected by an insertion of this report into the database. The column name is the key, with its value.'''
    cvs   = {}
    ents  = self.msg.entries
    for fx in ents:
      curfd = ents[fx]
      if curfd.several_fields:
        for vl in curfd.working_value:
          cvs[('%s_%s' % (fx, vl)).lower()] = vl
      else:
        try:
          cvs[fx] = curfd.working_value[0]
        except IndexError:
          raise Exception, ('No value supplied for column \'%s\' (%s)' % (fx, str(curfd)))
    return cvs

  # TODO: Consider the message field classes' declared default.
  def save(self):
    return self.__class__.sparse_matrix(self)

  def old_save(self):
    '''This method saves the report object into the table for that report class, returning the index as an integer.
It is not idempotent at this level; further constraints should be added by inheriting classes.'''
    tbl, cols = self.msg.__class__.create_in_db(self.__class__)
    cvs       = self.__insertables(cols)
    curz      = THE_DATABASE.cursor()
    cpt       = []
    vpt       = []
    for coln, _, escer, _ in cols:
      if coln in cvs:
        cpt.append(coln)
        vpt.append(escer.dbvalue(cvs[coln], curz))
    qry = 'INSERT INTO %s (%s) VALUES (%s) RETURNING indexcol;' % (tbl, ', '.join(cpt), ', '.join(vpt)) 
    curz.execute(qry)
    ans = curz.fetchone()[0]
    curz.close()
    return ans

  @classmethod
  def alter_condition(self, conds, ok):
    if not ok in conds:
      return ok, None
    idem    = lambda x: x
    newks   = {
      'type': ('report_type = %s', lambda x: NAME_MATCHING[x.name]),
      'type__name': ('report_type = %s', lambda x: NAME_MATCHING[x]),
      'nation__id': ('nation_pk = %s', idem),
      'created__lte': ('created_at <= %s', idem),
      'created__gte': ('created_at >= %s', idem),
      'type__name__in': ('report_type IN (%s)', lambda dem: ', '.join([NAME_MATCHING[x] for x in (dem or [''])])),
    }
    try:
      nk, nv  = newks[ok]
      return (nk, nv(conds[ok]) if type(nv) == type(idem) else nv)
    except KeyError:
      raise Exception, ('Specify adapter for %s (%s)' % (ok, conds[ok]))

  @classmethod
  def assemble_conditions(self, conds):
    curz  = postgres.cursor()
    ans   = []
    neg   = conds.pop('Invert Query', False)
    for cond in conds:
      rez = conds[cond]
      ans.append(curz.mogrify(cond, rez if type(rez) == type((1, 2)) else (rez,)))
    curz.close()
    return (' WHERE ' if conds else '') + (('NOT (%s)' if neg else '%s') % (' AND '.join(ans)))

  @classmethod
  def assemble_sort(self, dem):
    if not dem: return ''
    cn, dr = dem
    return ' ORDER BY %s %sENDING' % (cn, 'ASC' if dr else 'DESC')

  seen_actives  = {}
  @classmethod
  def active_columns(self, qid = None):
    if not qid: return qid
    if not qid in self.seen_actives: return None
    return seen_actives[qid]

  @classmethod
  def record_activity(self, qid, qnom):
    if not qid in self.seen_actives:
      self.seen_actives[qid] = set()
      return self.record_activity(qid, qnom)
    us  = self.seen_actives[qid]
    us.add(qnom)
    return us

  @classmethod
  def query(self, tn, djconds, **kwargs):
    tbl = self.ensure_table(tn)
    return ThouQuery(djconds, tn, **kwargs)

  @classmethod
  def find_matching_type(self, val, ctyp, cn = None):
    try:
      return {
        str:                ctyp,
        unicode:            ctyp,
        int:                'INTEGER /*NOT NULL*/',
        long:               'INTEGER /*NOT NULL*/',
        float:              'FLOAT /*NOT NULL*/',
        bool:               'BOOLEAN /*NOT NULL*/',
        Decimal:            'FLOAT /*NOT NULL*/',
        datetime.datetime:  'TIMESTAMP',
        datetime.date:      'TIMESTAMP WITHOUT TIME ZONE',
        datetime.time:      'TIMESTAMP'
      }[type(val)]
    except KeyError:
      raise Exception, ('Supply type for column %s (has a %s, %s)?' % (cn, str(type(val)), str(val)))

  @classmethod
  def decide_type(self, vl, cn = None):
    ctyp  = 'TEXT DEFAULT NULL'
    dval  = vl
    if type(vl) == type((None, 'INTEGER DEFAULT NULL')):
      ctyp  = vl[1]
      dval  = vl[0]
    elif type(vl) == type({'type':'INTEGER', 'null':False, 'default':'', 'value':None}):
      dval  = vl.get('value')
      ddef  = vl.get('default')
      dstr  = '%s %s%s' % (vl.get('type') or self.find_matching_type(dval, ctyp, cn), '' if vl.get('null') else 'NOT NULL', (' DEFAULT ' + ddef if ddef else ''))
      return self.decide_type((dval, dstr), cn)
    else:
      ctyp  = self.find_matching_type(vl, ctyp, cn)
      return self.decide_type((dval, ctyp), cn)
    return ctyp, dval

  @classmethod
  def batch(self):
    btc = ThouReportBatch()
    return btc

  @classmethod
  def store(self, tn, dat, **kwargs):
    if type(dat) in [type(x) for x in [set(), []]]:
      return [self.store(tn, cv, **kwargs) for cv in dat]
    if not dat: return None
    vals    = []
    curz    = postgres.cursor()
    btc     = kwargs.get('batch', None)
    tbl     = self.ensure_table(tn)
    ans     = dat.pop('indexcol', [])
    multid  = False
    # if type(ans) in [type(x) for x in [set(), []]]:
    if hasattr(ans, '__iter__'):
      multid  = True
    cols    = dat.keys()
    for col in cols:
      dval  = dat[col]
      col   = self.ensure_column(curz, tbl, col, dval)
      postgres.commit()
      elval = curz.mogrify('%s', (dval, ))
      if len(ans) > 0:
        dat[col]  = elval
      else:
        if btc and hasattr(dval, '__getitem__'):
          elval = dval.replace('\t', '\\t').replace('\n', '\\n')
        vals.append(elval)
    if vals:
      if btc:
        ans = btc.append(tbl, cols, vals)
      else:
        qry = ('INSERT INTO %s (%s) VALUES (%s) RETURNING indexcol;' % (tbl, ', '.join(cols), ', '.join(vals)))
        curz.execute(qry)
        ans = curz.fetchone()[0]
    else:
      bzt = ('UPDATE %s SET %s WHERE indexcol %s %s;' % (tbl, ', '.join(['%s = %s' % (k, dat[k]) for k in dat]), 'IN' if multid else '=', ('(%s)' % ', '.join(ans)) if multid else curz.mogrify('%s', (ans, ))))
      stderr.write('>>>\t%s\r\n' % (bzt, ))
      curz.execute(bzt)
    postgres.commit()
    curz.close()
    return ans

  seen_columns  = {}
  @classmethod
  def ensure_column(self, curz, tbl, col, dval, opts = {}):
    sncs  = self.seen_columns.get(tbl, set())
    if not col in sncs:
      curz.execute('SELECT TRUE FROM information_schema.columns WHERE table_name = %s AND column_name = %s', (tbl, col))
      if not curz.fetchone():
        word    = None
        # TODO: Scheduling the migrations.
        prepper = opts.get('prepper')
        if prepper:
          word  = prepper(tbl, col, dval)
        else:
          ctyp, dval  = self.decide_type(dval, col)
          word        = 'ALTER TABLE %s ADD COLUMN %s %s;' % (tbl, col, ctyp)
        curz.execute(word)
        sncs.add(col)
    self.seen_columns[tbl] = sncs
    return col

  seen_tables = set()
  @classmethod
  def ensure_table(self, tbl):
    try:
      if tbl in self.seen_tables: return tbl
      curz  = postgres.cursor()
      curz.execute('SELECT TRUE FROM information_schema.tables WHERE table_name = %s', (tbl,))
      if not curz.fetchone():
        curz.execute('CREATE TABLE %s (indexcol SERIAL NOT NULL, created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW());' % (tbl,))
        curz.close()
      postgres.commit()
      self.seen_tables.add(tbl)
      return tbl
    except Exception, e:
      raise Exception, ('Table creation: ' + str(e))

  @classmethod
  def sparse_matrix_store(self, hsh):
    tbl = self.ensure_table()
    return self.store(hsh, tbl)

  @classmethod
  def sparse_matrix(self, tn, cvs = None, prep = None):
    if not cvs:
      if issubclass(tn.__class__, self):
        tn, cols = self.msg.__class__.creation_sql(self.__class__)
        return self.sparse_matrix(tn, self.__insertables(cols), prep)
      raise ValueError, 'sparse_matrix wants a (string, hash), a (string, [hash]), or a (ThouReport). There is always an optional string after (super-table name).'
    # if type(cvs) == type([]):
    if hasattr(cvs, '__iter__'):
      return [self.sparse_matrix(tn, cv, prep) for cv in cvs]
    if type(cvs) == type({}):
      fxnms = {}
      for cvk in cvs:
        fxnms['%s_%s' % (tn, cvk)]  = cvs[cvk]
      return self.sparse_matrix_store(fxnms)
    raise Exception, ('What sparse matrix is %s?' % (str(cvs),))

  @classmethod
  def load(self, msgtxt):
    with ThouMessage.parse(msgtxt) as msg:
      return self(msg)

NAME_MATCHING = {
        'ANC':'ANC',
        'Birth':'BIR',
        'Case Management Response':'CMR',
        'Child Health':'CHI',
        'Community Based Nutrition':'CBN',
        'Community Case Management':'CCM',
        'Death':'DTH',
        'Departure':'DEP',
        'Newborn Care':'NBC',
        'PNC':'PNC',
        'Pregnancy':'PRE',
        'Red Alert':'RED',
        'Red Alert Result':'RAR',
        'Refusal':'REF',
        'Risk':'RISK',
        'Risk Result':'RES'
      }

class BasicConverter:
  def __init__(self, dft = {}):
    self.types    = ReportType.objects.all()
    self.defaults = dft
    self.thash    = self.__type_hash()

  def __type_hash(self):
    ans = self.defaults
    for t in self.types:
      ans[t.pk] = NAME_MATCHING[t.name]
    return ans

  def __getitem__(self, k):
    return self.thash[k]

class OldStyleReport:
  def __init__(self, rep, cur, cvr):
    self.autos  = rep
    self.conver = cvr
    self.cursor = cur
    self.ftypes = self.__type_hash()

  def __type_hash(self):
    ans = {}
    for fdt in FieldType.objects.all():
      it = {'category_pk': fdt.category.pk, 'key': fdt.key}
      ans[fdt.pk] = it
    return ans

  def __getitem__(self, k):
    return self.ftypes[k]

  @property
  def db_prepend(self):
    return self.conver[self.autos.type.pk].lower()

  @property
  def report_type(self):
    return self.conver[self.autos.type.pk]

  @property
  def message(self):
    return str(self)

  @property
  def reporter(self):
    return self.autos.reporter

  @property
  def patient(self):
    return self.autos.patient

  @property
  def hc(self):
    return self.autos.location

  @property
  def district(self):
    return self.autos.location.district

  @property
  def province(self):
    return self.autos.location.province

  @property
  def nation(self):
    return self.autos.location.nation

  # TODO:
  # 1.  The reports. With types. With fields.
  # 2.  Facilities.
  # 3.  Health workers
  # 4.  Locations
  # 5.  Reports.
  # 6.  Messages
  # 7.  Patients
  def __gather_fields(self, hsh = {}):
    fds = Field.objects.filter(report = self.autos)
    prp = self.db_prepend
    for fd in fds:
      ftype                         = fd.type
      cle                           = ftype.key
      typedata                      = self[ftype.pk]
      typedata[self.__val_name(fd)] = (fd.value or 0.0) if ftype.has_value else (fd.value and True or False)
      for td in typedata:
        # nom       = '%s_%s_%s' % (prp, cle, td)
        nom       = '%s_%s' % (cle, td)
        hsh[nom]  = typedata[td]
    return hsh

  def __val_name(self, fd):
    mid = 'bool'
    if fd.type.has_value:
      mid = ThouReport.find_matching_type(fd.value or 0.0, mid, None)
    return re.split(r'\s+', mid, 2)[0].lower()

  def __as_hash(self):
    ans = {}
    him = self.autos
    ans['report_type']        = self.report_type
    ans['former_pk']          = him.pk
    ans['reconstructed_msg']  = self.message
    ans['reporter_pk']        = self.reporter.pk
    ans['reporter_phone']     = self.reporter.telephone_moh
    ans['patient_id']         = self.patient.national_id
    ans['patient_pk']         = self.patient.pk
    ans['report_date']        = him.created
    if him.village:
      ans['village_pk']       = him.village.pk
    ans['health_center_pk']   = self.hc.pk
    ans['district_pk']        = self.district.pk
    if him.sector:
      ans['sector_pk']        = him.sector.pk
    ans['province_pk']        = self.province.pk
    if him.cell:
      ans['cell_pk']          = him.cell.pk
    ans['nation_pk']          = self.nation.pk
    if him.date:
      ans['lmp']              = him.date
    cls                       = copy.copy(ans)
    return ('%s_table' % (self.db_prepend,), cls, self.__gather_fields(ans))

  def convert(self, **kwargs):
    tbn, cls, dat = self.__as_hash()
    thr           = ThouReport.store(REPORTS_TABLE, cls, **kwargs)
    if not kwargs.get('batch'):
      # If we are batching, this fails.
      dat['log_id'] = thr
    suc           = ThouReport.store(tbn, dat, **kwargs)
    return (suc, thr, tbn)

  def __str__(self):
    return '%s ...' % (self.conver[self.autos.type.pk],)
