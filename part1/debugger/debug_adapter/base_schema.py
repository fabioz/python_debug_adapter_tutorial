class BaseSchema(object):
    
    def to_json(self):
        import json
        return json.dumps(self.to_dict())


requests_to_types = {}
all_messages = {}


def register(cls):
    all_messages[cls.__name__] = cls
    return cls

    
def register_request(request_command):

    def do_register(cls):
        requests_to_types[request_command] = cls
        return cls
    
    return do_register
    

def from_dict(dct):
    msg_type = dct.get('type')
    if msg_type is None:
        raise ValueError('Unable to make sense of message: %s' % (dct,))
    if msg_type == 'request':
        cls = requests_to_types[dct['command']] 
        return cls(**dct)

    
def from_json(json_msg):
    import json
    return from_dict(json.loads(json_msg))


def build_response(request, kwargs=None):
    if kwargs is None:
        kwargs = {}
    name = request.__class__.__name__
    assert name.endswith('Request')
    name = name[:-7] + 'Response'
    response_class = all_messages[name]
    kwargs.setdefault('seq', -1)
    return response_class(command=request.command, request_seq=request.seq, success=True, **kwargs)

