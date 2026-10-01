# Request log to stdout. %(U)s is the path without the query string, and the
# referer is left out, because URLs can carry values that must not be logged.
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms "%(a)s"'

# ponytail: 2 sync workers, one DB connection each (D28, ADR-0015): at most 2
# requests in flight and 2 Postgres connections. Upgrade path: psycopg_pool's
# ConnectionPool per worker (and more workers) when load needs it.
workers = 2
wsgi_app = "app:create_app()"
