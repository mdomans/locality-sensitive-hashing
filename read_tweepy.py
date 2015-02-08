import session, time, logging, hashlib
from collections import defaultdict

import tweepy
from tweepy import StreamListener
from tweepy.api import API
import twitter_settings

from google.appengine.api import users
from google.appengine.ext import ndb

from utils.deferred import deferred
from pipe_node import *
from lsh_matrix import *

APP_KEY = twitter_settings.consumer_key
APP_SECRET = twitter_settings.consumer_secret
DEFAULT_NUM_TWEETS = 200
REPORT_AFTER_COUNT = 40

class TwitterStatusListener(StreamListener):

    def __init__(self, api=None):
        StreamListener.__init__(self, api=api)
        self.api = api or API()
        self.tweets = []
        self.start_time = time.gmtime()
        self.prefix = str(int(time.time()))

    def on_connect(self):
        """Called once connected to streaming server.
        This will be invoked once a successful response
        is received from the server. Allows the listener
        to perform some work prior to entering the read loop.
        """
        logging.info("on_connect()")

    def on_status(self, status):
        """Called when a new status arrives"""
        text = status.text #.encode('utf-8')
        self.tweets.append(text) 
#         status = TwitterStatus(text = text)
#         status.put()
        #logging.info('status: %s', text)

        if len(self.tweets) >= DEFAULT_NUM_TWEETS:
            return False # this should trigger closing the connection
        else:
            return True

    def on_error(self, status_code):
        logging.info('Error: ' + str(status_code) + "\n")
        return False

    def on_timeout(self):
        logging.info("Timeout, sleeping for 60 seconds...\n")
        time.sleep(60)
        return

class TwitterLogin(session.BaseRequestHandler):
    def get(self):
        auth = tweepy.OAuthHandler(APP_KEY, APP_SECRET)
        # Redirect user to Twitter to authorize
        url = auth.get_authorization_url()
        logging.info("TwitterLogin url=%s", url)
        self.session['request_token_key'] = auth.request_token.key
        self.session['request_token_secret'] = auth.request_token.secret
        self.redirect(url)
    def post(self):
        self.get()

class TwitterLogout(session.BaseRequestHandler):
    def get(self):
        self.session['tw_auth'] = None
        self.redirect('/')
        return
    def post(self):
        self.get()

class TwitterCallback(session.BaseRequestHandler):
    def get_args(self):
        """
        All the args a request was called with
        """
        rqst = self.request
        args = rqst.arguments()
        resp = {}
        for arg in args:
            resp[arg] = repr(rqst.get_all(arg))
        return resp
    def get(self):
        resp = self.get_args()
        rqst = self.request
        verifier = rqst.get('oauth_verifier')

        auth = tweepy.OAuthHandler(APP_KEY, APP_SECRET)
        auth.set_request_token(self.session['request_token_key'], self.session['request_token_secret'])

        logging.info('Callback came with %s', resp)
        try:
            auth.get_access_token(verifier)
        except tweepy.TweepError:
            logging.error('Error! Failed to get access token.')
        
        self.session['tw_auth'] = auth
        self.session['tw_status'] = 'Logged In and Ready'
        self.redirect('/')

class TwitterGetTweets(session.BaseRequestHandler):
    def get_tweets(self):
        auth = self.session['tw_auth']
        api = tweepy.API(auth)
        listen = TwitterStatusListener(api)

        #note, tried doing secure=False which is not support by twitter api this gives an
        # error for the sample.json end_point
        stream = tweepy.Stream(auth, listen)
        logging.info("getting stream now!")

        try:
            stream.sample()
        except tweepy.TweepError:
            logging.error("error with streaming api")
            stream.disconnect()
        return (listen.tweets)

    def post(self):
        self.get()

class Tweet(ndb.Model):
    t = ndb.TextProperty()
    def str(self):
        return self.t
    
class DemoUserInfo(ndb.Model):
    asof = ndb.DateTimeProperty(auto_now_add=True)
    user_id = ndb.StringProperty()
    email = ndb.StringProperty()
    nickname = ndb.StringProperty()
    ds_key = ndb.StringProperty()
    calculating = ndb.BooleanProperty(default = False)
    calc_done = ndb.BooleanProperty(default = False)
    def filename(self):
        return 'user_id: {user_id}, email: {email}, nickname: {nickname}, asof: {asof}' \
            .format(asof = self.asof.isoformat()[:19], user_id = self.user_id, email = self.email, nickname = self.nickname)
    @classmethod
    def latest_for_user(cls, user):
        dui = cls.query(cls.user_id == user.user_id()).order(-cls.asof).get()
        return dui
    def purge(self):
        ds_key = self.ds_key
        matrix = Matrix.find(ds_key = self.ds_key)
        if matrix:
            matrix.purge()
        tweet_keys = Tweet.query(ancestor = self.key).fetch(keys_only = True)
        ndb.delete_multi(tweet_keys)

class TwitterReadNode(TwitterGetTweets, PipeNode):
    def Open(self):
        logging.info('TwitterReadNode.Open() have twitter token: %s', 'yes' if 'tw_auth' in self.session else 'no')
        if not ('tw_auth' in self.session):
            logging.error('Not logged in into twitter')
            raise NotLoggedIn("Not logged in into twitter")
        auth = self.session['tw_auth']
        api = tweepy.API(auth)
        if not api:
            logging.error('API not found')
            raise NotLoggedIn("Not logged in into twitter")
        
        # Read tweets from the stream
        logging.info('TwitterReadNode.Open using %s', auth)
        self.tweets = super(TwitterReadNode, self).get_tweets()
        self.cursor = 0
        self.count = len(self.tweets)
        
        logging.info('TwitterReadNode.Open completed')
    
    def GetNext(self):
        if self.cursor < self.count:
            tweet = self.tweets[self.cursor]
            if 0 == (self.cursor % REPORT_AFTER_COUNT):
                logging.info('TwitterReadNode.GetNext (%d) = %s', self.cursor, tweet)
            self.cursor += 1
            return tweet
        raise NotFound('Tweets exhausted')
    
    def Close(self, save = False):
        logging.info('TwitterReadNode.Close()')
        if save:
            # DemoUserInfo
            u = users.get_current_user()
            old_dui = DemoUserInfo.latest_for_user(u)
            dui = DemoUserInfo(user_id = u.user_id(), email = u.email(), nickname = u.nickname())
            duik = dui.put()
            self.session['duik'] = duik.urlsafe()
            tweets = [ Tweet(parent = duik, t = t) for t in self.tweets ]
            keys = ndb.put_multi(tweets)
            if old_dui:
                old_dui.purge()
                old_dui.key.delete()

        logging.info('TwitterReadNode.Close completed')
        banner = 'Tweets as of %s GMT' % time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()) 
        self.session['tw_status'] = banner
        self.session['tweets'] = '<br/>\n&mdash; '.join(self.tweets)
        logging.info('TwitterReadNode.Close after (%d) tweets', len(self.tweets))
        self.session['fetched'] = True
        self.session['calc_done'] = False
        self.redirect('/')
    
    def get(self):
        logging.info('TwitterReadNode.get()')
        self.post()

    def post(self):
        logging.info('TwitterReadNode.post()')
        try:
            self.Open()
        except:
            self.session['tw_auth'] = None
            self.redirect('/')
            return
        
        while True:
            try:
                self.GetNext()
            except NotFound as nf:
                logging.info('TwitterReadNode.GetNext completed, %s', nf.value)
                break
        
        self.Close(save = True)
#         self.Close()

class TweetLine(object):
    @staticmethod
    def parse(tweet):
        doc_id = str(tweet.key.id())
        text = tweet.t
        text = text.lower()
        text = ' '.join(text.split())
        return doc_id, text

def lsh_iter(LineFormat, iterator, ds_key):
    matrix = Matrix.find(ds_key)
    logging.info('<TextWorker filename={filename}>'\
        .format(filename = matrix.filename))

    line_count = 0
    for line in iterator:
        line_count += 1
        stats = {}
        doc_id, text = LineFormat.parse(line)
        doc = matrix.create_doc(doc_id, text, stats)
        if 0 == (line_count % 80):
            logging.debug('<Tweet count %d, id %s, text %s />', line_count, doc_id, text)
        stats = {}

    logging.info('</TextWorker filename={filename}>'\
        .format(filename = matrix.filename))

    if matrix.file_key:
        duik = ndb.Key(urlsafe = matrix.file_key)
        dui = duik.get()
        dui.ds_key = ds_key
        dui.calc_done = True
        dui.calculating = False
        dui.put()

def lsh_report(ds_key, duik):
    def report(tweet_set_buckets, tweet_sets):
        msg = ''
        for set_hash in tweet_set_buckets:
            msg += '\nFor %d tweets, %d buckets: %s' % \
            (len(tweet_sets[set_hash]), len(tweet_set_buckets[set_hash]), tweet_set_buckets[set_hash])
            tweets = ndb.get_multi(tweet_sets[set_hash])
            tweet_text_list = [tw.t for tw in tweets]
            tweet_text_set = set(tweet_text_list)
            for tweet_text in tweet_text_set:
                tweet_ids = [tweet.key.id() for tweet in tweets if tweet.t == tweet_text]
                msg += '\n    %s' % tweet_ids
                msg += '\n    %s' % tweet_text
        return msg
                    
    def display(bkt, tweets):
        lines = []
        lines.append('bucket: %d' % bkt)
        for tweet in tweets:
            lines.append('    %s' % tweet.t)
        return '\n'.join(lines)

    try:
        matrix = Matrix.find(ds_key)
#     logging.debug(str(matrix))
        matrix_rows = matrix.find_child_rows()
    except AttributeError:
        logging.error('Unable to find matrix_rows for ds_key %s', ds_key)
        raise
    dui = ndb.Key(urlsafe = duik).get()
    dui_id = ndb.Key(urlsafe = duik).id()
#     logging.debug('LshTweets %s, %d, %d rows', dui, dui_id, len(matrix_rows))
    bucket_tweets = defaultdict(list)

    row_count = 0
    for matrix_row in matrix_rows:
        row_count += 1
        for bkt in matrix_row.buckets:
            bucket_tweets[bkt].append(int(matrix_row.doc_id))
    bkt_count = len(bucket_tweets.keys())
    logging.info('LshTweets %s for %d rows has %d buckets', dui, row_count, bkt_count)
    tweet_sets = {}
    tweet_set_buckets = defaultdict(list)
    for bkt in bucket_tweets:
        if len(bucket_tweets[bkt]) > 1:
            tweet_ids = bucket_tweets[bkt]
#             logging.debug('tweet_ids = %s', tweet_ids)
            tweet_keys = [ndb.Key(DemoUserInfo, dui_id, Tweet, _id) for _id in tweet_ids]
            composite_set_key = ''.join(sorted([tk.urlsafe() for tk in tweet_keys]))
            set_hash = '%07d' % (int(hashlib.md5(composite_set_key).hexdigest(), 16) % 10000000)
            tweet_sets[set_hash] = tweet_keys
            tweet_set_buckets[set_hash].append(bkt)
    retval = report(tweet_set_buckets, tweet_sets)
    logging.info(retval)
    return retval
#             logging.debug('tweet_ids = %s', tweet_keys)
#             tweets = ndb.get_multi(tweet_keys)
#             logging.info(display(bkt, tweets))
            
class LshTweets(session.BaseRequestHandler):
    @staticmethod
    def calc(session):
        duik = session['duik']
        dui = ndb.Key(urlsafe = duik).get() if duik else None
        logging.info('LshTweets %s', dui)
        tweet_qry = Tweet.query(ancestor = dui.key).fetch()
        Matrix._initialize()
        MatrixRow._initialize()
        matrix = Matrix.create(filename = dui.filename(), 
                               source = 'tweets', file_key = duik,
                               rows=5, bands=15, shingle_type='c4', minhash_modulo=7001)
        ds_key = matrix.ds_key

        if matrix:
            dui.calc_done = False
            dui.calculating = True
            dui.put()
            deferred.defer(lsh_iter, TweetLine, tweet_qry, ds_key)

    @staticmethod
    def show(session):
        duik = session['duik']
        dui = ndb.Key(urlsafe = duik).get() if duik else None
        try:
            ds_key = dui.ds_key
            session['lsh_results'] = lsh_report(ds_key, duik)
        except AttributeError: 
            session['lsh_results'] = 'Error has occurred. Staff has been notified.'
            logging.error('LshTweets.show unable to find dui key %s', duik)
#         ds_key = Matrix.make_new_id(source = 'tweets', filename = str(dui))
        
#         deferred.defer(lsh_report, ds_key, dui.key)

#     def get(self):
#         LshTweets.calc_lsh_results(self.session)
#         self.redirect('/')
# 
#     def post(self):
#         LshTweets.calc_lsh_results(self.session)
#         self.redirect('/')

urls = [
     ('/twitter_login', TwitterLogin),
     ('/twitter_callback', TwitterCallback),
#      ('/twitter_get_tweets', TwitterGetTweets),
     ('/twitter_read_node', TwitterReadNode),
     ('/twitter_logout', TwitterLogout),
#      ('/lsh_tweets', LshTweets),
]
