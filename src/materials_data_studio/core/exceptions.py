"""Application-specific exceptions."""


class MaterialsDataStudioError(Exception):
    """Base class for expected application errors."""


class DataImportError(MaterialsDataStudioError):
    """Raised when imported data cannot be interpreted safely."""


class InvalidAxisConversionError(MaterialsDataStudioError):
    """Raised when a requested physical axis conversion is invalid."""


class SessionFormatError(MaterialsDataStudioError):
    """Raised for invalid, unsupported, or corrupted sessions."""


class CifValidationError(MaterialsDataStudioError):
    """Raised when a CIF edit would create an invalid structure."""


class AnalysisError(MaterialsDataStudioError):
    """Raised when a numerical analysis cannot be completed."""

