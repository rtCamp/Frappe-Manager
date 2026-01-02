"""
Tests for frappe_manager.ssl_manager.certificate_link_manager module.

This module tests the CertificateLinkManager class which handles creating
and managing symlinks between Let's Encrypt certificates and nginx-proxy.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, call
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig


class TestCertificateLinkManagerInitialization:
    """Tests for CertificateLinkManager initialization."""
    
    def test_init_stores_storage_config(self, tmp_path):
        """Test that initialization stores the storage config."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        # Create required directories
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        assert manager.storage_config == storage_config
    
    def test_init_validates_certs_dir_exists(self, tmp_path):
        """Test that initialization validates certs directory exists."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        # Create only ssl_dir, not certs_dir
        storage_config.ssl_dir.mkdir(parents=True)
        
        with pytest.raises(ValueError, match="Certificate directory does not exist"):
            CertificateLinkManager(storage_config)
    
    def test_init_validates_ssl_dir_exists(self, tmp_path):
        """Test that initialization validates SSL directory exists."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        # Create only certs_dir, not ssl_dir
        storage_config.certs_dir.mkdir(parents=True)
        
        with pytest.raises(ValueError, match="SSL directory does not exist"):
            CertificateLinkManager(storage_config)
    
    def test_init_with_both_directories_existing(self, tmp_path):
        """Test successful initialization when both directories exist."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        # Create both directories
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        assert manager is not None


class TestCertificateLinkManagerLinkCertificate:
    """Tests for CertificateLinkManager.link_certificate method."""
    
    def test_link_certificate_creates_symlinks_for_primary_domain(self, mocker, tmp_path):
        """Test that link_certificate creates symlinks for the primary domain."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        # Create directories
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        letsencrypt_dir = storage_config.ssl_dir / "letsencrypt"
        letsencrypt_dir.mkdir()
        
        # Mock create_symlink
        mock_create_symlink = mocker.patch('frappe_manager.ssl_manager.certificate_link_manager.create_symlink')
        
        manager = CertificateLinkManager(storage_config)
        
        privkey_path = letsencrypt_dir / "privkey.pem"
        fullchain_path = letsencrypt_dir / "fullchain.pem"
        
        manager.link_certificate(
            cert_type="letsencrypt",
            domain="example.com",
            privkey_path=privkey_path,
            fullchain_path=fullchain_path
        )
        
        # Verify create_symlink was called twice (once for key, once for cert)
        assert mock_create_symlink.call_count == 2
        
        # Check the calls
        calls = mock_create_symlink.call_args_list
        
        # First call should be for privkey
        assert calls[0][0][0] == Path("/etc/letsencrypt/letsencrypt/privkey.pem")
        assert calls[0][0][1] == storage_config.certs_dir / "example.com.key"
        
        # Second call should be for fullchain
        assert calls[1][0][0] == Path("/etc/letsencrypt/letsencrypt/fullchain.pem")
        assert calls[1][0][1] == storage_config.certs_dir / "example.com.crt"
    
    def test_link_certificate_with_alias_domains(self, mocker, tmp_path):
        """Test that link_certificate creates symlinks for alias domains."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        letsencrypt_dir = storage_config.ssl_dir / "letsencrypt"
        letsencrypt_dir.mkdir()
        
        mock_create_symlink = mocker.patch('frappe_manager.ssl_manager.certificate_link_manager.create_symlink')
        
        manager = CertificateLinkManager(storage_config)
        
        privkey_path = letsencrypt_dir / "privkey.pem"
        fullchain_path = letsencrypt_dir / "fullchain.pem"
        
        manager.link_certificate(
            cert_type="letsencrypt",
            domain="example.com",
            privkey_path=privkey_path,
            fullchain_path=fullchain_path,
            alias_domains=["www.example.com", "api.example.com"]
        )
        
        # Should be called 6 times: 2 for primary + 2*2 for aliases
        assert mock_create_symlink.call_count == 6
        
        calls = mock_create_symlink.call_args_list
        
        # Check primary domain (first 2 calls)
        assert calls[0][0][1] == storage_config.certs_dir / "example.com.key"
        assert calls[1][0][1] == storage_config.certs_dir / "example.com.crt"
        
        # Check first alias (calls 2-3)
        assert calls[2][0][1] == storage_config.certs_dir / "www.example.com.key"
        assert calls[3][0][1] == storage_config.certs_dir / "www.example.com.crt"
        
        # Check second alias (calls 4-5)
        assert calls[4][0][1] == storage_config.certs_dir / "api.example.com.key"
        assert calls[5][0][1] == storage_config.certs_dir / "api.example.com.crt"
    
    def test_link_certificate_with_empty_alias_list(self, mocker, tmp_path):
        """Test that link_certificate handles empty alias domain list."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        letsencrypt_dir = storage_config.ssl_dir / "letsencrypt"
        letsencrypt_dir.mkdir()
        
        mock_create_symlink = mocker.patch('frappe_manager.ssl_manager.certificate_link_manager.create_symlink')
        
        manager = CertificateLinkManager(storage_config)
        
        privkey_path = letsencrypt_dir / "privkey.pem"
        fullchain_path = letsencrypt_dir / "fullchain.pem"
        
        manager.link_certificate(
            cert_type="letsencrypt",
            domain="example.com",
            privkey_path=privkey_path,
            fullchain_path=fullchain_path,
            alias_domains=[]
        )
        
        # Should only be called twice for the primary domain
        assert mock_create_symlink.call_count == 2


class TestCertificateLinkManagerUnlinkCertificate:
    """Tests for CertificateLinkManager.unlink_certificate method."""
    
    def test_unlink_certificate_removes_primary_domain_symlinks(self, mocker, tmp_path):
        """Test that unlink_certificate removes symlinks for the primary domain."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        # Create mock symlinks
        privkey_link = storage_config.certs_dir / "example.com.key"
        fullchain_link = storage_config.certs_dir / "example.com.crt"
        
        # Touch files to simulate symlinks
        privkey_link.touch()
        fullchain_link.touch()
        
        manager.unlink_certificate("example.com")
        
        # Verify symlinks were removed
        assert not privkey_link.exists()
        assert not fullchain_link.exists()
    
    def test_unlink_certificate_with_alias_domains(self, mocker, tmp_path):
        """Test that unlink_certificate removes symlinks for alias domains."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        # Create mock symlinks for primary and alias domains
        domains = ["example.com", "www.example.com", "api.example.com"]
        for domain in domains:
            (storage_config.certs_dir / f"{domain}.key").touch()
            (storage_config.certs_dir / f"{domain}.crt").touch()
        
        manager.unlink_certificate(
            "example.com",
            alias_domains=["www.example.com", "api.example.com"]
        )
        
        # Verify all symlinks were removed
        for domain in domains:
            assert not (storage_config.certs_dir / f"{domain}.key").exists()
            assert not (storage_config.certs_dir / f"{domain}.crt").exists()
    
    def test_unlink_certificate_handles_nonexistent_symlinks(self, tmp_path):
        """Test that unlink_certificate doesn't fail if symlinks don't exist."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        # Don't create any symlinks, just try to unlink
        manager.unlink_certificate("example.com")
        
        # Should not raise any errors


class TestCertificateLinkManagerGetCertificatePaths:
    """Tests for CertificateLinkManager.get_certificate_paths method."""
    
    def test_get_certificate_paths_resolves_symlinks(self, tmp_path):
        """Test that get_certificate_paths resolves symlinks correctly."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        letsencrypt_dir = storage_config.ssl_dir / "letsencrypt"
        letsencrypt_dir.mkdir()
        
        # Create actual certificate files
        actual_privkey = letsencrypt_dir / "privkey.pem"
        actual_fullchain = letsencrypt_dir / "fullchain.pem"
        actual_privkey.touch()
        actual_fullchain.touch()
        
        # Create symlinks
        privkey_link = storage_config.certs_dir / "example.com.key"
        fullchain_link = storage_config.certs_dir / "example.com.crt"
        
        # Create symlinks pointing to container paths
        container_privkey = Path("/etc/letsencrypt/letsencrypt/privkey.pem")
        container_fullchain = Path("/etc/letsencrypt/letsencrypt/fullchain.pem")
        
        privkey_link.symlink_to(container_privkey)
        fullchain_link.symlink_to(container_fullchain)
        
        manager = CertificateLinkManager(storage_config)
        
        privkey_path, fullchain_path = manager.get_certificate_paths("example.com")
        
        # Should resolve back to host paths
        assert privkey_path == actual_privkey
        assert fullchain_path == actual_fullchain
    
    def test_get_certificate_paths_raises_if_symlink_missing(self, tmp_path):
        """Test that get_certificate_paths raises error if symlink doesn't exist."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        with pytest.raises(FileNotFoundError):
            manager.get_certificate_paths("nonexistent.com")


class TestCertificateLinkManagerPathConversion:
    """Tests for path conversion methods."""
    
    def test_host_to_container_path(self, tmp_path):
        """Test conversion from host path to container path."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        host_path = storage_config.ssl_dir / "letsencrypt" / "live" / "example.com" / "privkey.pem"
        container_path = manager._host_to_container_path(host_path, "letsencrypt")
        
        expected = Path("/etc/letsencrypt/letsencrypt/live/example.com/privkey.pem")
        assert container_path == expected
    
    def test_container_to_host_path(self, tmp_path):
        """Test conversion from container path to host path."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        container_path = Path("/etc/letsencrypt/letsencrypt/live/example.com/privkey.pem")
        host_path = manager._container_to_host_path(container_path)
        
        expected = storage_config.ssl_dir / "letsencrypt" / "live" / "example.com" / "privkey.pem"
        assert host_path == expected


class TestCertificateLinkManagerSafeUnlink:
    """Tests for _safe_unlink method."""
    
    def test_safe_unlink_removes_existing_file(self, tmp_path):
        """Test that _safe_unlink removes an existing file."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        test_file = tmp_path / "test.txt"
        test_file.touch()
        
        manager._safe_unlink(test_file)
        
        assert not test_file.exists()
    
    def test_safe_unlink_ignores_missing_file(self, tmp_path):
        """Test that _safe_unlink doesn't raise error for missing files."""
        storage_config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            certs_dir=tmp_path / "certs",
            ssl_dir_container=Path("/etc/letsencrypt"),
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot"
        )
        
        storage_config.ssl_dir.mkdir(parents=True)
        storage_config.certs_dir.mkdir(parents=True)
        
        manager = CertificateLinkManager(storage_config)
        
        nonexistent_file = tmp_path / "nonexistent.txt"
        
        # Should not raise error
        manager._safe_unlink(nonexistent_file)
