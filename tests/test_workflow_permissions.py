from pathlib import Path
import unittest
import yaml


class WorkflowPermissionsTests(unittest.TestCase):
    def test_package_write_is_available_only_to_production_promotion(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        promotion = yaml.load((root / 'promote.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(promotion['permissions']['packages'], 'write')
        self.assertEqual(promotion['jobs']['promote']['environment'], 'production')
        self.assertEqual(promotion['jobs']['promote']['if'], "github.event_name == 'workflow_dispatch'")
        for name in ['candidate.yml', 'validate.yml']:
            workflow = yaml.load((root / name).read_text(), Loader=yaml.BaseLoader)
            self.assertEqual(workflow['permissions']['packages'], 'read')
