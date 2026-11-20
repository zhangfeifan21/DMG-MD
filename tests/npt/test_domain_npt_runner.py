import unittest
from run_domain_npt import check_route


class RouteTest(unittest.TestCase):
    def test_local_requires_both_exact_records(self):
        check_route('DMGMD_DOMAIN mode=m2a\nDMGMD_DOMAIN_NPT mode=affine\n', 2, 'affine')
        for log in ('DMGMD_DOMAIN mode=m1-fallback\nDMGMD_DOMAIN_NPT mode=affine',
                    'DMGMD_DOMAIN mode=m2a\n',
                    'DMGMD_DOMAIN mode=m2a\nDMGMD_DOMAIN_NPT mode=rebuild'):
            with self.assertRaises(ValueError): check_route(log, 2, 'affine')

    def test_off_cannot_claim_local_npt(self):
        check_route('DMGMD_DOMAIN mode=m1-fallback\n', 2, 'off')
        with self.assertRaises(ValueError):
            check_route('DMGMD_DOMAIN mode=m2a\nDMGMD_DOMAIN_NPT mode=affine', 2, 'off')


if __name__ == '__main__': unittest.main()
