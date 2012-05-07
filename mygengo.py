"""Interface to the myGengo translation API.

Sandbox example:

>>> config = ConfigParser.RawConfigParser({'sandbox': '1'})
>>> config_file = os.path.join(os.path.dirname(__file__), 'mygengo.ini')
>>> dummy = config.read(config_file)
>>> api_key = config.get('config', 'api_key')
>>> private_key = config.get('config', 'private_key')
>>> sandbox = config.getboolean('config', 'sandbox')
>>> client = Client(api_key, private_key, sandbox=sandbox)

>>> float(client.get_account_balance()) > 0
True
>>> job = client.submit_job('big red car', 'fr', tier='machine', auto_approve=True)
>>> job['body_src']
u'big red car'
>>> job = client.get_job(job['job_id'], pre_mt=True)
>>> job['status']
u'approved'
>>> job['body_tgt']
u'grande voiture rouge'

"""

import hashlib
import hmac
import logging
import time
import urllib
import urllib2

try:
    # Prefer simplejson as it's usually faster
    import simplejson as json
except ImportError:
    import json

_api_url = 'http://api.mygengo.com/v1/'
_sandbox_api_url = 'http://api.sandbox.mygengo.com/v1/'

class Error(Exception):
    """API exception base class."""

    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        self.args = (code, msg)

    def __str__(self):
        return 'Error {0}: {1}'.format(self.code, self.msg)

class ConnectionError(Error):
    """Error connecting to API at the HTTP level."""
    pass

class JsonError(Error):
    """Error parsing the JSON returned from myGengo."""
    pass

class MygengoError(Error):
    """A myGengo error. For a list of error codes, see:
    http://mygengo.com/services/api/dev-docs/error-codes
    
    """
    pass

class MethodRequest(urllib2.Request):
    """Subclass Request so we can override get_method() to allow non-GET/POST methods."""

    def __init__(self, method, *args, **kwargs):
        self._method = method
        urllib2.Request.__init__(self, *args, **kwargs)

    def get_method(self):
        return self._method

class Client(object):
    TIME_BETWEEN_REQUESTS = 0.5
    NUM_TRIES = 3
    TIME_BETWEEN_TRIES = 5.0

    @staticmethod
    def _check_json(response, field):
        """Ensure field is in response, raise JsonError if not."""
        if field not in response:
            raise JsonError(-2, "Bad JSON: {0!r} not in response".format(field))

    def __init__(self, api_key, private_key, sandbox=False):
        """Initialize a myGengo Client with given API and private keys. If sandbox is
        True, use myGengo's sandbox API rather than the real thing.

        """
        self._api_url = _sandbox_api_url if sandbox else _api_url
        self._api_key = api_key
        self._private_key = private_key
        self._last_request_time = None

    def _add_api_key(self, params):
        """Add API key and timestamp to given params dict."""
        params['api_key'] = self._api_key
        params['ts'] = str(int(time.time()))

    def _api_sig(self, query):
        """Return myGengo authentication signature based on given query string."""
        query_hmac = hmac.new(self._private_key, query, hashlib.sha1)
        return query_hmac.hexdigest()

    def _wait_between_requests(self):
        """Wait a bit between each request for poor old MyGengo."""
        # If we don't wait between requests, MyGengo says HTTP 503:
        # "please wait a short while before issuing the next request"
        now = time.time()
        if (self._last_request_time and
                now - self._last_request_time < self.TIME_BETWEEN_REQUESTS):
            delta = self.TIME_BETWEEN_REQUESTS - (now - self._last_request_time)
            time.sleep(delta)
        self._last_request_time = now

    def _request(self, method, path, params=None, parse_json=True, timeout=10):
        """Perform a myGengo request with given method, path, and params dict. Not
        intended to be used directly -- use Client.get_job() and the like instead.
        
        """
        self._wait_between_requests()

        if params is None:
            params = {}

        if method in ('POST', 'PUT'):
            # myGengo expects params['data'] to be serialized using JSON
            params = {'data': json.dumps(params, separators=(',', ':'), sort_keys=True)}
            self._add_api_key(params)
            # Serialize using JSON again to calculate signature
            data = json.dumps(params, separators=(',', ':'), sort_keys=True)
            # myGengo API seems to calculate signature with slashes escaped (not
            # required by JSON, but allowed)
            data = data.replace('/', r'\/')
            params['api_sig'] = self._api_sig(data)
            data = urllib.urlencode(params)
            query = None
        else:
            # Need to sort and de-unicode the params to calculate signature
            params = params.copy()
            self._add_api_key(params)
            utf8_params = []
            for key, value in sorted(params.iteritems()):
                utf8_params.append((key, value.encode('utf-8')))
            query = urllib.urlencode(utf8_params)
            query += '&api_sig=' + self._api_sig(query)
            data = None

        # Send request to API
        headers = {'Accept': 'application/json'}
        url = self._api_url + path
        if query:
            url += '?' + query
        request = MethodRequest(method, url, data=data, headers=headers)

        num_tries = self.NUM_TRIES
        while num_tries > 0:
            num_tries -= 1
            try:
                f = urllib2.urlopen(request, timeout=timeout)
                response = f.read()
                break
            except urllib2.HTTPError as error:
                if error.code in (500, 503) and num_tries > 0:
                    # Service temporarily unavailable, retry in a bit (this happens quite
                    # a bit with MyGengo)
                    logging.info('HTTP error {0}, waiting {1}s and trying again'.format(
                            error.code, self.TIME_BETWEEN_TRIES))
                else:
                    raise
            except IOError as error:
                raise ConnectionError(-1, "Couldn't connect: {0}".format(error))

            time.sleep(self.TIME_BETWEEN_TRIES)

        if not parse_json:
            return response

        # Parse JSON response
        try:
            response = json.loads(response)
        except ValueError as error:
            raise JsonError(-2, "Couldn't parse JSON: {0}".format(error))

        # Check that it wasn't an error
        self._check_json(response, 'opstat')
        if response['opstat'] not in ('ok', 'error'):
            raise JsonError(-2, "Bad JSON: 'opstat' is {0}".format(response['opstat']))
        if response['opstat'] == 'error':
            self._check_json(response, 'err')
            self._check_json(response['err'], 'code')
            self._check_json(response['err'], 'msg')
            raise MygengoError(response['err']['code'], response['err']['msg'])

        # Return the response field
        self._check_json(response, 'response')
        return response['response']

    def get_account_stats(self):
        """Return account statistics, such as credits spent. See also:
        http://mygengo.com/services/api/dev-docs/methods/account-stats-get
        
        """
        return self._request('GET', 'account/stats')

    def get_account_balance(self):
        """Return the account credit balance (as a string). See also:
        http://mygengo.com/services/api/dev-docs/methods/account-balance-get

        """
        response = self._request('GET', 'account/balance')
        self._check_json(response, 'credits')
        return response['credits']

    def get_job_preview(self, job_id, filename=None):
        """Return data for JPEG preview image of translated text for given job. Write
        data to output filename if given, otherwise return data as string. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-revisions-get
        
        """
        image_data = self._request('GET', 'translate/job/{0}/preview'.format(job_id),
                                   parse_json=False)
        if filename is not None:
            with open(filename, 'wb') as f:
                f.write(image_data)
        else:
            return image_data

    def get_job_revision(self, job_id, revision_id):
        """Return the given revision for the given job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-revision-rev-id-get
        
        """
        return self._request('GET', 'translate/job/{0}/revision/{1}'.format(job_id,
                revision_id))

    def get_job_revisions(self, job_id):
        """Return the list of revisions for the given job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-revisions-get
        
        """
        response = self._request('GET', 'translate/job/{0}/revisions'.format(job_id))
        self._check_json(response, 'revisions')
        return response['revisions']

    def get_job_feedback(self, job_id):
        """Return the feedback submitted for the given job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-feedback-get
        
        """
        response = self._request('GET', 'translate/job/{0}/feedback'.format(job_id))
        self._check_json(response, 'feedback')
        return response['feedback']

    def submit_job_comment(self, job_id, comment):
        """Submit a new comment on the given job's comment thread. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-comment-post
        
        """
        return self._request('POST', 'translate/job/{0}/comment'.format(job_id),
                             params={'body': comment})

    def get_job_comments(self, job_id):
        """Return the list of comments in given job's comment thread. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-comments-get
        
        """
        response = self._request('GET', 'translate/job/{0}/comments'.format(job_id))
        self._check_json(response, 'thread')
        return response['thread']

    def cancel_job(self, job_id):
        """Cancel the given job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-delete
        
        """
        self._request('DELETE', 'translate/job/{0}'.format(job_id))

    def get_job(self, job_id, pre_mt=False):
        """Return a specific job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-get
        
        """
        params = {'pre_mt': '1'} if pre_mt else None
        response = self._request('GET', 'translate/job/{0}'.format(job_id),
                                 params=params)
        self._check_json(response, 'job')
        return response['job']

    def update_job(self, job_id, action, **other_params):
        """Update the given job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-id-put
        
        """
        params = {'action': action}
        params.update(other_params)
        return self._request('PUT', 'translate/job/{0}'.format(job_id), params=params)

    def submit_job(self, text=None, target=None, source='en', tier='machine', slug=None,
                   auto_approve=False, custom_data=None, comment=None, callback_url=None,
                   job=None):
        """Submit a job for translation. If the content has already been translated this
        will return the existing job. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-job-post
        
        """
        if job is None:
            job = {'body_src': text,
                   'lc_src': source,
                   'lc_tgt': target,
                   'tier': tier,
                   'auto_approve': str(int(auto_approve))}
            if slug is not None:
                job['slug'] = slug
            if custom_data is not None:
                job['custom_data'] = custom_data
            if comment is not None:
                job['comment'] = comment
            if callback_url is not None:
                job['callback_url'] = callback_url
        response = self._request('POST', 'translate/job', params={'job': job}, timeout=30)
        self._check_json(response, 'job')
        return response['job']

    def get_job_group(self, job_group_id):
        """Return a list of jobs that were previously submitted with submit_job_group().
        See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-jobs-id-get
        
        """
        response = self._request('GET', 'translate/jobs/{0}'.format(job_group_id))
        self._check_json(response, 'jobs')
        return response['jobs']
    
    def get_jobs(self, status=None, timestamp_after=None, count=None):
        """Return a list of jobs filtered by the given parameters. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-jobs-get
        
        """
        params = {}
        if status is not None:
            params['status'] = status
        if timestamp_after is not None:
            params['timestamp_after'] = str(timestamp_after)
        if count is not None:
            params['count'] = str(count)
        return self._request('GET', 'translate/jobs', params=params)

    def submit_job_group(self, jobs, as_group=False, process=True):
        """Submit a group of jobs to be translated together. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-jobs-post
        
        """
        params = {'jobs': jobs,
                  'as_group': str(int(as_group)),
                  'process': str(int(process))}
        response = self._request('POST', 'translate/jobs', params=params, timeout=300)
        return response

    def get_language_pairs(self, source=None):
        """Return list of supported language pairs, filtering by source language if
        given. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-service-language-pairs-get
        
        """
        params = {'lc_src': source} if source else None
        return self._request('GET', 'translate/service/language_pairs', params=params)

    def get_languages(self):
        """Return list of supported languages and their language codes/names. See also:
        http://mygengo.com/services/api/dev-docs/methods/translate-service-languages-get
        
        """
        return self._request('GET', 'translate/service/languages')

if __name__ == '__main__':
    # Simple command line interface to the API
    import ConfigParser
    import os
    import pprint
    import sys

    if len(sys.argv) <= 1:
        print """Usage: mygengo.py function [arg1] [arg2] ... [kw1=arg1] [kw2=arg2] ...

Examples:
  mygengo.py get_account_balance
  mygengo.py get_languages
  mygengo.py submit_job "This is a test" es auto_approve=1
  mygengo.py get_job 8754 pre_mt=1
  mygengo.py get_job_preview 8754 filename=preview.jpg

Uses 'mygengo.ini' for API keys and sandbox setting. Example config:
  [config]
  api_key = <Your API key>
  private_key = <Your private key>
  sandbox = 1  ; this is the default, sandbox=0 to use real API"""
        sys.exit(1)

    config = ConfigParser.RawConfigParser({'sandbox': '1'})
    config_file = os.path.join(os.path.dirname(__file__), 'mygengo.ini')
    config.read(config_file)
    api_key = config.get('config', 'api_key')
    private_key = config.get('config', 'private_key')
    sandbox = config.getboolean('config', 'sandbox')
    client = Client(api_key, private_key, sandbox=sandbox)

    args = []
    kwargs = {}
    for arg in sys.argv[2:]:
        if '=' in arg:
            key, value = arg.split('=', 1)
            kwargs[key] = value
        else:
            args.append(arg)

    function = getattr(client, sys.argv[1])
    response = function(*args, **kwargs)
    pprint.pprint(response)
