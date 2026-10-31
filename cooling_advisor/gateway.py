"""Dashboard-to-API client. No database credentials or workflow execution in the UI."""
import os
import httpx


class BackendError(Exception):
    pass


class Backend:
    def __init__(self, token=None):
        self.url = os.getenv('COOLING_API_URL','http://127.0.0.1:8000').rstrip('/')
        self.token = token or os.getenv('COOLING_API_TOKEN','')

    def request(self, method, path, payload=None, key=None):
        headers = {'Authorization':'Bearer '+self.token}
        if key:
            headers['Idempotency-Key'] = key
        try:
            response = httpx.request(method,self.url+path,json=payload,headers=headers,timeout=160)
            if response.is_error:
                try:
                    detail = response.json().get('detail','Request failed')
                except ValueError:
                    detail = 'Backend returned an invalid response'
                raise BackendError(str(detail)[:600])
            return response.json()
        except httpx.RequestError:
            raise BackendError('Backend is unavailable. Start the workspace with python -m cooling_advisor.')

    def get(self,path):
        return self.request('GET',path)

    def post(self,path,payload,key=None):
        return self.request('POST',path,payload,key)
