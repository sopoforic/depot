from datetime import datetime
from email.utils import parsedate_tz, mktime_tz
from time import gmtime, time
from .manager import get_depot

_BLOCK_SIZE = 4096 * 64 # 256K


class _FileIter(object):
    def __init__(self, file, block_size):
        self.file = file
        self.block_size = block_size

    def __iter__(self):
        return self

    def next(self):
        val = self.file.read(self.block_size)
        if not val:
            raise StopIteration
        return val

    __next__ = next # py3

    def close(self):
        self.file.close()


class FileServeApp(object):
    """
    Serves a static filelike object.
    """
    def __init__(self, storedfile, cache_max_age):
        self.file = storedfile

        self.last_modified = self.file.last_modified
        self.content_length = self.file.content_length
        self.content_type = self.file.content_type
        self.cache_expires = cache_max_age

    def generate_etag(self):
        return '"%s-%s"' % (self.last_modified, self.content_length)

    def parse_date(self, value):
        try:
            return mktime_tz(parsedate_tz(value))
        except (TypeError, OverflowError):
            raise RuntimeError("Received an ill-formed timestamp")

    @classmethod
    def make_date(cls, d):
        if isinstance(d, datetime):
            d = d.utctimetuple()
        else:
            d = gmtime(d)

        return '%s, %02d%s%s%s%s %02d:%02d:%02d GMT' % (
            ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[d.tm_wday],
            d.tm_mday, ' ',
            ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
             'Oct', 'Nov', 'Dec')[d.tm_mon - 1],
            ' ', str(d.tm_year), d.tm_hour, d.tm_min, d.tm_sec)


    def has_been_modified(self, environ, etag, last_modified):
        if environ['REQUEST_METHOD'] not in ('GET', 'HEAD'):
            return False

        unmodified = False

        modified_since = environ.get('HTTP_IF_MODIFIED_SINCE')
        if modified_since:
            modified_since = self.parse_date(modified_since)
            if last_modified and last_modified <= modified_since:
                unmodified = True

        if_none_match = environ.get('HTTP_IF_NONE_MATCH')
        if if_none_match and etag == if_none_match:
            unmodified = True

        return not unmodified

    def __call__(self, environ, start_response):
        headers = []
        timeout = self.cache_expires
        etag = self.generate_etag()
        headers += [('Etag', '%s' % etag),
            ('Cache-Control', 'max-age=%d, public' % timeout)]

        try:
            has_been_modified = self.has_been_modified(environ, etag, self.last_modified)
        except:
            start_response('400 Bad Request', [('Content-Type', 'text/html')])
            return ['''\
<html>
 <head>
  <title>400 Bad Request</title>
 </head>
 <body>
  <h1>400 Bad Request</h1>
  ETag was malformed in request
 </body>
</html>''']



        if not has_been_modified:
            file.close()
            start_response('304 Not Modified', headers)
            return []

        headers.extend((
            ('Expires', self.make_date(time() + timeout)),
            ('Content-Type', self.content_type),
            ('Content-Length', str(self.content_length)),
            ('Last-Modified', self.make_date(self.last_modified))
            ))
        start_response('200 OK', headers)
        return environ.get('wsgi.file_wrapper', _FileIter)(file, _BLOCK_SIZE)


class DepotMiddleware(object):
    def __init__(self, app, mountpoint='/depot', cache_max_age=3600*24*7):
        self.app = app
        self.mountpoint = mountpoint
        self.cache_max_age = cache_max_age

    def _404_response(self, start_response):
        start_response('404 Not Found', [('Content-Type', 'text/html')])
        return ['''\
        <html>
         <head>
          <title>404 Not Found</title>
         </head>
         <body>
          <h1>404 Not Found</h1>
          File Not Found
         </body>
        </html>''']

    def _301_response(self, start_response, location):
        start_response('301 Moved Permanently', [('Content-Type', 'text/html'),
                                                 ('Location', location)])
        return ['''\
        <html>
         <head>
          <title>301 Moved Permanently</title>
         </head>
         <body>
          <h1>301 Moved Permanently</h1>
          File you are looking for is available at <a href="%s">%s</a>
         </body>
        </html>''' % (location, location)]

    def __call__(self, environ, start_response):
        full_path = environ['PATH_INFO']

        if not full_path.startswith(self.mountpoint):
            return self.app(environ, start_response)

        path = full_path.split('/')
        if len(path) < 3:
            return self._404_response(start_response)

        __, depot, fileid = full_path[:3]
        depot = get_depot(depot)
        if not depot:
            return self._404_response(start_response)

        try:
            f = depot.get(file)
        except IOError:
            return self._404_response(start_response)

        public_url = f.public_url
        if public_url is not None:
            self._301_response(start_response, public_url)

        return FileServeApp(f, self.cache_max_age)(environ, start_response)