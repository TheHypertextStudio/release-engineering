import json
import unittest
import xml.etree.ElementTree as ET
from studio.recovery import remove_build,replace_current,SPARKLE

class RecoveryTests(unittest.TestCase):
    def test_withdrawal_removes_only_the_named_build_and_retries_safely(self):
        feed=f'<rss xmlns:sparkle="{SPARKLE}"><channel><item><enclosure sparkle:version="42" url="old" /></item><item><enclosure sparkle:version="43" url="new" /></item></channel></rss>'
        result=remove_build(feed,42)
        self.assertEqual([item.get('url') for item in ET.fromstring(result).findall('channel/item/enclosure')],['new'])
        self.assertEqual(remove_build(result,42),result)

    def test_recovery_uses_the_read_generation_as_write_precondition(self):
        calls=[]
        def runner(command,**kwargs):
            calls.append(command)
            if 'describe' in command: return json.dumps({'generation':'17'})
            if 'cat' in command: return 'old'
        replace_current('bucket','curfew/appcast.xml',lambda content:b'new',runner=runner)
        self.assertIn('gs://bucket/curfew/appcast.xml#17',calls[1])
        self.assertIn('--if-generation-match=17',calls[2])

    def test_old_candidate_cannot_overwrite_new_release_metadata(self):
        calls=[]
        def runner(command,**kwargs):
            calls.append(command)
            return json.dumps({'generation':'17'}) if 'describe' in command else 'new release'
        replace_current('bucket','curfew/release.json',lambda content:None,runner=runner)
        self.assertEqual(len(calls),2)
