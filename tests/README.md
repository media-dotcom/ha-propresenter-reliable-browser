The pure backend tests can run without installing Home Assistant:

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_*.py'
```

The integration-specific tests in CI use Home Assistant's test environment;
these focused tests cover normalization and cache behavior independently.
