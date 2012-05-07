mygengo
=======

[Oyster.com's](http://www.oyster.com/) Python interface to the myGengo translation API. Example:

```python
>>> import mygengo
>>> client = mygengo.Client(api_key, private_key)
>>> client.get_account_balance()
'42.50'
>>> client.submit_job('This is a test', 'fr', auto_approve=True)
{'job_id': '1234', ...}
>>> client.get_job(1234)
{'body_tgt': "Il s'agit d'un test", ...}
```
