"""
Tests for kospex schema
"""
import kospex_schema as KospexSchema


class TestPackageUseConstants:
    def test_all_constants_are_strings(self):
        for name in ("PACKAGE_USE_DIRECT", "PACKAGE_USE_DEV", "PACKAGE_USE_PEER",
                     "PACKAGE_USE_OPTIONAL", "PACKAGE_USE_TRANSITIVE"):
            assert isinstance(getattr(KospexSchema, name), str), f"{name} must be a str"

    def test_package_use_values_is_frozenset(self):
        assert isinstance(KospexSchema.PACKAGE_USE_VALUES, frozenset)

    def test_all_constants_in_values_set(self):
        for const in (KospexSchema.PACKAGE_USE_DIRECT, KospexSchema.PACKAGE_USE_DEV,
                      KospexSchema.PACKAGE_USE_PEER, KospexSchema.PACKAGE_USE_OPTIONAL,
                      KospexSchema.PACKAGE_USE_TRANSITIVE):
            assert const in KospexSchema.PACKAGE_USE_VALUES

    def test_values_set_has_five_members(self):
        assert len(KospexSchema.PACKAGE_USE_VALUES) == 5
