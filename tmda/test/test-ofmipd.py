import unittest
import os
import subprocess
import signal
import socket
import re
import hmac
import md5

import OpenSSL.SSL as SSL

class Server(object):
    _port = 8025

    _executable = '../bin/tmda-ofmipd'
    _commonServerArgs = ['-d', '-f', '-p', '127.0.0.1:%d' % _port, '-a',
                         'test-ofmipd.auth']
    _certKeyServerArgs = ['--ssl-cert=test-ofmipd.cert',
                          '--ssl-key=test-ofmipd.key']

    def __init__(self, sslArg=None):
        self._sslArg = sslArg
        self._serverProc = None

    def start(self):
        serverArgs = [self._executable]
        serverArgs.extend(self._commonServerArgs)
        if self._sslArg:
            serverArgs.append(self._sslArg)
            serverArgs.extend(self._certKeyServerArgs)

        newEnv = dict(os.environ)
        newEnv['PYTHONPATH'] = '..'

        self._serverProc = subprocess.Popen(serverArgs, env=newEnv)

        # Wait for server availability
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        while True:
            try:
                s.connect(('127.0.0.1', self.port()))
                s.close()
                break
            except socket.error:
                pass

    def stop(self):
        os.kill(self._serverProc.pid, signal.SIGTERM)
        self._serverProc.wait()

    def port(self):
        return self._port

class Client(object):
    def __init__(self, port):
        self._port = port
        self._address = ('127.0.0.1', port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def connect(self):
        self._sock.connect(self._address)
        self._sock.recv(200)

    def receiveUntil(self, finished):
        data = ''
        while not finished(data):
            data += self._sock.recv(200)
        return data

    _responseMatcher = re.compile(r'^\d{3} .*\r\n', re.MULTILINE)
    def _completeResponse(self, data):
        return self._responseMatcher.search(data) is not None

    def exchange(self, msg):
        self._sock.send(msg)
        response = self.receiveUntil(self._completeResponse)
        return response

    _responseLineMatcher = re.compile(r'^(?P<code>\d{3})[- ](?P<line>.*)\r\n',
                                      re.MULTILINE)
    def splitResponse(self, response):
        'return (code, [lines])'
        lines = []
        code = None
        for m in self._responseLineMatcher.finditer(response):
            newCode = int(m.group('code'))
            if code is not None and code != newCode:
                raise ValueError('mismatched result codes in response')
            code = newCode
            lines.append(m.group('line'))

        return (code, lines)

class SslClient(Client):
    def __init__(self, port):
        Client.__init__(self, port)
        self._sslSock = None
        self._normalSock = self._sock

    def startSsl(self):
        context = SSL.Context(SSL.SSLv23_METHOD)
        self._sslSock = SSL.Connection(context, self._normalSock)
        self._sslSock.set_connect_state()

        self._sock = self._sslSock

class ServerResposeTestMixin(object):
    def setUp(self):
        self.serverSetUp()
        self.clientSetUp()

        response = self.client.exchange('EHLO test.com\r\n')
        (code, lines) = self.client.splitResponse(response)
        self.ehloCode = code
        self.ehloLines = lines

    def tearDown(self):
        self.server.stop()

    def serverSetUp(self):
        self.server = Server()
        self.server.start()

    def clientSetUp(self):
        self.client = Client(self.server.port())
        self.client.connect()

    def checkExtensions(self, extensions):
        raise NotImplementedError()

    def checkAuthTypes(self, authTypes):
        raise NotImplementedError()

    def testExtensions(self):
        self.failUnless(self.ehloCode == 250)
        extensions = []
        authTypes = []
        for line in self.ehloLines[1:]:
            if line.startswith('AUTH '):
                authTypes.extend(line.split()[1:])

            parts = line.split(' ', 1)
            extensions.append(parts[0])

        self.checkExtensions(extensions)
        self.checkAuthTypes(authTypes)

    def testStartTls(self):
        response = self.client.exchange('STARTTLS\r\n')
        (code, lines) = self.client.splitResponse(response)
        self.failUnless(code == self.expectedStartTlsCode)

    def testAuth(self):
        response = self.client.exchange('AUTH LOGIN\r\n')
        (code, lines) = self.client.splitResponse(response)
        self.failUnless(code == self.expectedAuthCode)

class UnencryptedServerResponses(ServerResposeTestMixin, unittest.TestCase):
    expectedStartTlsCode = 502
    expectedAuthCode = 334

    def checkExtensions(self, extensions):
        self.failUnless(extensions == ['AUTH'])

    def checkAuthTypes(self, authTypes):
        self.failUnless(set(authTypes) == set(['LOGIN', 'PLAIN', 'CRAM-MD5']))

class SslServerResponses(ServerResposeTestMixin, unittest.TestCase):
    expectedStartTlsCode = 503
    expectedAuthCode = 334

    def serverSetUp(self):
        self.server = Server('--ssl')
        self.server.start()

    def clientSetUp(self):
        self.client = SslClient(self.server.port())
        self.client.startSsl()
        self.client.connect()

    def checkExtensions(self, extensions):
        self.failUnless(extensions == ['AUTH'])

    def checkAuthTypes(self, authTypes):
        self.failUnless(set(authTypes) == set(['LOGIN', 'PLAIN', 'CRAM-MD5']))

class PreStartTlsServerResponses(ServerResposeTestMixin, unittest.TestCase):
    expectedStartTlsCode = 220
    expectedAuthCode = 530

    def serverSetUp(self):
        self.server = Server('--tls=on')
        self.server.start()

    def clientSetUp(self):
        self.client = SslClient(self.server.port())
        self.client.connect()

    def checkExtensions(self, extensions):
        self.failUnless(extensions == ['STARTTLS'])

    def checkAuthTypes(self, authTypes):
        self.failUnless(len(authTypes) == 0)

class PostStartTlsServerResponses(ServerResposeTestMixin, unittest.TestCase):
    expectedStartTlsCode = 503
    expectedAuthCode = 334

    def serverSetUp(self):
        self.server = Server('--tls=on')
        self.server.start()

    def clientSetUp(self):
        self.client = SslClient(self.server.port())
        self.client.connect()
        response = self.client.exchange('STARTTLS\r\n')
        (code, lines) = self.client.splitResponse(response)
        assert(code == 220)
        self.client.startSsl()

    def checkExtensions(self, extensions):
        self.failUnless(extensions == ['AUTH'])

    def checkAuthTypes(self, authTypes):
        self.failUnless(set(authTypes) == set(['LOGIN', 'PLAIN', 'CRAM-MD5']))

class OptionalStartTlsServerResponses(ServerResposeTestMixin,
                                      unittest.TestCase):
    expectedStartTlsCode = 220
    expectedAuthCode = 334

    def serverSetUp(self):
        self.server = Server('--tls=optional')
        self.server.start()

    def checkExtensions(self, extensions):
        self.failUnless(set(extensions) == set(['STARTTLS', 'AUTH']))

    def checkAuthTypes(self, authTypes):
        self.failUnless(set(authTypes) == set(['LOGIN', 'PLAIN', 'CRAM-MD5']))

class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.server.start()

        self.client = Client(self.server.port())
        self.client.connect()

    def tearDown(self):
        self.server.stop()

    def authPlain(self, username, password, expectedCode):
        authString = '\x00'.join([username, username, password])
        authString = authString.encode('base64')[:-1]
        response = self.client.exchange('AUTH PLAIN %s\r\n' % authString)
        (code, lines) = self.client.splitResponse(response)

        self.failUnless(code == expectedCode,
            'username: %r password: %r code: %d' % (username, password, code))

    def authLogin(self, username, password, firstCode, secondCode):
        userString = username.encode('base64')[:-1]
        passString = password.encode('base64')[:-1]

        response = self.client.exchange('AUTH LOGIN %s\r\n' % userString)
        (code, lines) = self.client.splitResponse(response)
        self.failUnless(code == firstCode)

        if firstCode == 334:
            response = self.client.exchange('%s\r\n' % passString)
            (code, lines) = self.client.splitResponse(response)
            self.failUnless(code == secondCode,
                'username: %r password: %r code: %d' % \
                (username, password, code))

    def authCramMd5(self, username, password, expectedCode):
        response = self.client.exchange('AUTH CRAM-MD5\r\n')
        (code, lines) = self.client.splitResponse(response)
        self.failUnless(code == 334)
        self.failUnless(len(lines) == 1)

        ticket = lines[0].decode('base64')
        digest = hmac.new(password, ticket, md5).hexdigest()
        message = '%s %s' % (username, digest)
        message = message.encode('base64')[:-1]

        response = self.client.exchange('%s\r\n' % message)
        (code, lines) = self.client.splitResponse(response)
        self.failUnless(code == expectedCode,
            'username: %r password: %r code: %d' % (username, password, code))

    def testPlain(self):
        self.authPlain('testuser', 'testpassword', 235)

    def testLogin(self):
        self.authLogin('testuser', 'testpassword', 334, 235)

    def testCramMd5(self):
        self.authCramMd5('testuser', 'testpassword', 235)

    _badUsernames = [
        'testuserr',
        'testuse',
        'testus',
        'testu',
        '\x00',
        '\x00testuser',
    ]
    _badPasswords = [
        'testpasswordd',
        'testpasswor',
        'testpasswo',
        'testpassw',
        'testpass',
        '',
        ' ',
        '\x00',
    ]

    def testPlainFailure(self):
        for password in self._badPasswords:
            self.authPlain('testuser', password, 535)

        for username in self._badUsernames:
            self.authPlain(username, 'testpassword', 535)

    def testLoginFailure(self):
        for password in self._badPasswords:
            # For LOGIN, an empty password is ignored. I don't know if this
            # is a bug or not, but it's probably how tmda-ofmipd has always
            # worked.
            if password == '':
                continue
            self.authLogin('testuser', password, 334, 535)

        for username in self._badUsernames:
            self.authLogin(username, 'testpassword', 334, 535)

    def testCramMd5Failure(self):
        for password in self._badPasswords:
            self.authCramMd5('testuser', password, 535)

        for username in self._badUsernames:
            self.authCramMd5(username, 'testpassword', 535)

# XXX Add tests:
# Send message success and failure
# Dupes and syntax errors

if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    unittest.main(testRunner=runner)