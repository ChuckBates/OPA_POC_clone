# OPA Proofs Of Concept

This repository is intended as a proofs of concept only! These are *NOT* finished products nor anything more than examples on how OPA *might* handle authorization requests.

This directory contains POCs for the following scenarios:
- A stand alone OPA service with a Python server that uses the OPA Compile API and Evaluate API to perform
data filtering and authorization checks respectively
- An Aserto Edge Authorizer with a Python server that uses the Edge Authorizer's Compile and Evaluate API endpoints. 

Both of these POCs behave generally the same: 
- When the python server receives a data filter API request it asks OPA/Aserto Edge for a set of conditions to apply to the SQL query that serves the request. 
- When the python server recieves an authorization request it asks OPA/Aserto Edge to make an authorization decision based on the rego policies.

The server itself is implemented in Python using Flask.

Contents:
- `opa_poc.insomnia.json` A collect of preconfigured insomnia requests to test the expected behaviors of both `Stand Alone OPA Service` and `Aserto Edge Authorizer` scenarios. 
- `Stand Alone OPA Service` The first POC described above with a stand alone OPA service.
- `Aserto Edge Authorizer` The second POC described above expecting an Aserto Edge Authorizer.

Further details for each POC is found in respective Readme files located in those folders.


