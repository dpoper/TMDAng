import os
import sys

rootDir = '..'
userDir = os.path.join('home', 'testuser')
filesDir = 'files'

def fixupFiles():
    '''
    Fix permissions and such that don't get saved in source control.
    '''

    os.chmod(os.path.join(userDir, '.tmda', 'crypt_key'), 0600)
    os.chmod(os.path.join(filesDir, 'test-ofmipd.auth'), 0600)

def fixupPythonPath():
    sys.path.append(rootDir)

def fixupHome():
    os.environ['HOME'] = userDir

def testPrep():
    fixupFiles()
    fixupHome()
    fixupPythonPath()
