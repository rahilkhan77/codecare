# CodeCare maintenance fixture

A tiny Python app with a **deliberate** `eval` bug so scans, diagnosis, sandbox tests, and patches can be exercised without a GitHub App.

```
python -m pytest -q
```

The test fails until `add` stops using `eval`.
