OpenLSH
=======

Stages
------

OpenLSH is an open source platform dessigned. It encompasses an end-to-end architecture comprising the following _stages_:
 1. Data mining from social media: Twitter, LinkedIn, Github, &hellip;,
 2. Filtering incoming data,
 3. Shingling,
 4. Minhashes,
 5. Locality sensitive hashing and
 6. Candidate matching.

The OpenLSH framework is designed with a pipelining architecture and be extensible.

Flexible Pipelining
-------------------

Each of the stages listed above is implemented using operators which can run as independent threads. 
Each operator can be implemented as an iterator, which is class with three three methods that allows a consumer of the result of the physical operator to get the result one _item_ at a time. The three methods forming the iterator
for an operation are:

 1. 0pen (). This method starts the process of getting items, but does not get
    an item. It initializes any data structures needed to perform the operation
    and calls 0pen() for any arguments of the operation.
 2. GetNext (). This method returns the next item in the result and adjusts
    data structures as necessary to allow subsequent items to be obtained.
    In getting the next item of its result, it typically calls GetNext() one
    or more times on its argument(s). If there are no more items to return,
    GetNext () returns a special value NotFound, which we assume cannot be
    mistaken for a item.
 3. Close (). This method ends the iteration after all items, or all items that the consumer wanted, have been obtained. Typically, it calls Close () on any arguments of the operator.

`lshIterator` is a base class from which the above stages are derived. It defines Open(), GetNext(), and
Close() methods on instances of the class. Each stage class, in turn, provides a default implementation for its function (mining, filtering, etc). The flexibility of the OpenLSH framework comes from the fact that the the default base classes representing each stage can be further inherited and modified for specific implementations.

The code, especially the GetNext () method, makes heavy use of the `yield` command in Python. In case you are not familiar with it, [here is an explanation] (http://stackoverflow.com/questions/231767/the-python-yield-keyword-explained).

Implementation thus far
-----------------------

The problems we had with using the streaming API have been resolved and the code has been updated.

To read tweets,
 1. Download the repo
 2. Get your own consumer_key and consumer_secret from the Twitter App [Registration page](https://apps.twitter.com/).
 3. Set up callback URL appropriately.
 4. Change application id in app.yaml and push the code
 5. Visit <your application id>.appspot.com/get_tweets in your favorite browser.
 6. You will need to give permission to invoke the Twitter API on your behalf
 7. The tweets will be visible in the logs. Go to https://appengine.google.com/ and navigate to logs for your application.
 
