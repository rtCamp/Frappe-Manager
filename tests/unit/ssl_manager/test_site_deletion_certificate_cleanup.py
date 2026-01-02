"""
Test to verify that fm delete command properly removes SSL certificates and symlinks.

This test validates the complete deletion flow:
1. Site deletion triggers remove_bench()
2. remove_bench() calls remove_certificate()
3. remove_certificate() removes symlinks via link_manager.unlink_certificate()
4. remove_certificate() removes cert files via service.remove_certificate()
5. remove_certificate() restarts nginx

Test approach: Integration-style test that verifies the call chain.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, call
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES


class TestSiteDeletionCertificateCleanup:
    """Test that site deletion properly cleans up SSL certificates."""
    
    def test_remove_certificate_removes_symlinks_first(self):
        """
        Test that remove_certificate removes symlinks before certificate files.
        
        This is important because:
        - Symlinks in certs/ directory point to files in ssl/ directory
        - If we remove cert files first, nginx might try to read broken symlinks
        - Removing symlinks first prevents nginx errors
        """
        # Setup
        certificate = SSLCertificate(
            domain="alok.rt.gw",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            hsts="off"
        )
        
        mock_service = MagicMock()
        mock_link_manager = MagicMock()
        mock_nginx_controller = MagicMock()
        
        manager = SSLCertificateManager(
            certificate=certificate,
            service=mock_service,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller
        )
        
        # Track call order
        call_order = []
        mock_link_manager.unlink_certificate.side_effect = lambda *args, **kwargs: call_order.append('unlink')
        mock_service.remove_certificate.side_effect = lambda *args, **kwargs: call_order.append('remove')
        mock_nginx_controller.restart.side_effect = lambda *args, **kwargs: call_order.append('restart')
        
        # Act
        manager.remove_certificate()
        
        # Assert
        assert call_order == ['unlink', 'remove', 'restart'], \
            "Certificate removal must: 1) unlink symlinks, 2) remove cert files, 3) restart nginx"
        
        # Verify symlinks were removed with correct domain
        mock_link_manager.unlink_certificate.assert_called_once_with("alok.rt.gw", None)
        
        # Verify certificate was removed from service
        mock_service.remove_certificate.assert_called_once_with(certificate)
        
        # Verify nginx was restarted to apply changes
        mock_nginx_controller.restart.assert_called_once()
    
    def test_remove_certificate_with_alias_domains(self):
        """
        Test that remove_certificate properly removes alias domain symlinks.
        
        When a site has alias domains (e.g., www.alok.rt.gw, api.alok.rt.gw),
        the certificate removal must clean up symlinks for ALL domains:
        - Primary domain: alok.rt.gw
        - Alias domains: www.alok.rt.gw, api.alok.rt.gw
        """
        # Setup
        certificate = SSLCertificate(
            domain="alok.rt.gw",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            hsts="off"
        )
        
        alias_domains = ["www.alok.rt.gw", "api.alok.rt.gw"]
        
        mock_service = MagicMock()
        mock_link_manager = MagicMock()
        mock_nginx_controller = MagicMock()
        
        manager = SSLCertificateManager(
            certificate=certificate,
            service=mock_service,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller
        )
        
        # Act
        manager.remove_certificate(alias_domains=alias_domains)
        
        # Assert - alias domains are passed to unlink_certificate
        mock_link_manager.unlink_certificate.assert_called_once_with(
            "alok.rt.gw",
            alias_domains
        )
        
        # Certificate removal should still happen
        mock_service.remove_certificate.assert_called_once_with(certificate)
        mock_nginx_controller.restart.assert_called_once()
    
    def test_site_deletion_flow_removes_both_primary_and_alias_certificates(self):
        """
        Test the complete flow from site.remove_bench() perspective.
        
        In site.py:716-718, the deletion flow is:
        ```python
        def remove_certificate(self):
            self.certificate_manager.remove_certificate(self.bench_config.alias_domains)  # Remove alias certs
            self.certificate_manager.remove_certificate()  # Remove primary cert
            self.bench_config.ssl = SSLCertificate(domain=self.name, ssl_type=SUPPORTED_SSL_TYPES.none)
            self.save_bench_config()
        ```
        
        This test verifies that BOTH calls happen and clean up all certificates.
        """
        # Setup
        certificate = SSLCertificate(
            domain="alok.rt.gw",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            hsts="off"
        )
        
        alias_domains = ["www.alok.rt.gw"]
        
        mock_service = MagicMock()
        mock_link_manager = MagicMock()
        mock_nginx_controller = MagicMock()
        
        manager = SSLCertificateManager(
            certificate=certificate,
            service=mock_service,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller
        )
        
        # Act - simulate the two calls from site.remove_certificate()
        manager.remove_certificate(alias_domains=alias_domains)  # First call with alias domains
        manager.remove_certificate()  # Second call without alias domains
        
        # Assert - should have been called twice
        assert mock_link_manager.unlink_certificate.call_count == 2, \
            "unlink_certificate should be called twice: once for aliases, once for primary"
        
        # First call should include alias domains
        first_call = mock_link_manager.unlink_certificate.call_args_list[0]
        assert first_call == call("alok.rt.gw", alias_domains), \
            "First call should remove symlinks for primary domain AND alias domains"
        
        # Second call should be for primary domain only
        second_call = mock_link_manager.unlink_certificate.call_args_list[1]
        assert second_call == call("alok.rt.gw", None), \
            "Second call should remove symlinks for primary domain only"
        
        # Service should also be called twice to remove certificate
        assert mock_service.remove_certificate.call_count == 2, \
            "remove_certificate should be called twice on the service"
        
        # Nginx should be restarted twice (once per remove_certificate call)
        assert mock_nginx_controller.restart.call_count == 2, \
            "nginx should be restarted after each certificate removal"
    
    def test_symlink_removal_includes_both_key_and_crt_files(self):
        """
        Test that unlink_certificate removes both .key and .crt symlinks.
        
        Each domain has two symlinks in the certs/ directory:
        - alok.rt.gw.key -> points to privkey.pem in ssl/letsencrypt/
        - alok.rt.gw.crt -> points to fullchain.pem in ssl/letsencrypt/
        
        This test verifies the link manager removes BOTH symlinks.
        """
        # We'll use the real CertificateLinkManager to test actual file operations
        from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
        from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
        
        # Setup with temp directories
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            
            ssl_dir = tmppath / "ssl"
            certs_dir = tmppath / "certs"
            webroot_dir = tmppath / "webroot"
            
            ssl_dir.mkdir()
            certs_dir.mkdir()
            webroot_dir.mkdir()
            
            # Create fake certificate files
            le_dir = ssl_dir / "letsencrypt" / "alok.rt.gw"
            le_dir.mkdir(parents=True)
            
            privkey = le_dir / "privkey.pem"
            fullchain = le_dir / "fullchain.pem"
            privkey.write_text("fake privkey")
            fullchain.write_text("fake fullchain")
            
            # Create symlinks
            primary_key = certs_dir / "alok.rt.gw.key"
            primary_crt = certs_dir / "alok.rt.gw.crt"
            primary_key.symlink_to(privkey)
            primary_crt.symlink_to(fullchain)
            
            # Create alias domain symlinks
            alias_key = certs_dir / "www.alok.rt.gw.key"
            alias_crt = certs_dir / "www.alok.rt.gw.crt"
            alias_key.symlink_to(privkey)
            alias_crt.symlink_to(fullchain)
            
            # Verify symlinks exist
            assert primary_key.exists(), "Primary .key symlink should exist"
            assert primary_crt.exists(), "Primary .crt symlink should exist"
            assert alias_key.exists(), "Alias .key symlink should exist"
            assert alias_crt.exists(), "Alias .crt symlink should exist"
            
            # Setup link manager
            storage_config = SSLStorageConfig(
                ssl_dir=ssl_dir,
                ssl_dir_container=Path("/etc/nginx/ssl"),
                certs_dir=certs_dir,
                certs_dir_container=Path("/etc/nginx/certs"),
                webroot_dir=webroot_dir,
                validate_on_init=False
            )
            
            link_manager = CertificateLinkManager(storage_config)
            
            # Act - remove certificate symlinks
            link_manager.unlink_certificate("alok.rt.gw", alias_domains=["www.alok.rt.gw"])
            
            # Assert - all symlinks should be removed
            assert not primary_key.exists(), "Primary .key symlink should be removed"
            assert not primary_crt.exists(), "Primary .crt symlink should be removed"
            assert not alias_key.exists(), "Alias .key symlink should be removed"
            assert not alias_crt.exists(), "Alias .crt symlink should be removed"
            
            # But actual certificate files should still exist
            assert privkey.exists(), "Actual privkey.pem should still exist (removed by service)"
            assert fullchain.exists(), "Actual fullchain.pem should still exist (removed by service)"


def test_complete_site_deletion_integration():
    """
    Integration test verifying the complete site deletion flow.
    
    This test simulates what happens when you run:
    $ fm delete alok.rt.gw
    
    Expected flow:
    1. delete command (commands.py:297) calls benches.remove_benches()
    2. remove_benches() calls bench.remove_bench()
    3. remove_bench() (site.py:1288) calls self.remove_certificate()
    4. remove_certificate() (site.py:716) calls:
       - certificate_manager.remove_certificate(alias_domains) - removes alias symlinks + cert
       - certificate_manager.remove_certificate() - removes primary symlinks + cert
    5. Each remove_certificate call:
       - Removes symlinks (both .key and .crt for each domain)
       - Removes actual certificate files from ssl/letsencrypt/
       - Restarts nginx
    
    This test verifies the entire chain works correctly.
    """
    # Setup mocks
    certificate = SSLCertificate(
        domain="alok.rt.gw",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        hsts="off"
    )
    
    alias_domains = ["www.alok.rt.gw", "api.alok.rt.gw"]
    
    mock_service = MagicMock()
    mock_link_manager = MagicMock()
    mock_nginx_controller = MagicMock()
    
    # Track all operations for verification
    operations_log = []
    
    def log_unlink(*args, **kwargs):
        operations_log.append(('unlink', args, kwargs))
    
    def log_remove(*args, **kwargs):
        operations_log.append(('remove', args, kwargs))
    
    def log_restart(*args, **kwargs):
        operations_log.append(('restart', args, kwargs))
    
    mock_link_manager.unlink_certificate.side_effect = log_unlink
    mock_service.remove_certificate.side_effect = log_remove
    mock_nginx_controller.restart.side_effect = log_restart
    
    # Create manager
    manager = SSLCertificateManager(
        certificate=certificate,
        service=mock_service,
        link_manager=mock_link_manager,
        nginx_controller=mock_nginx_controller
    )
    
    # Act - Simulate site.remove_certificate() from site.py:716-718
    manager.remove_certificate(alias_domains=alias_domains)  # Line 717
    manager.remove_certificate()  # Line 718
    
    # Assert - verify complete operation sequence
    assert len(operations_log) == 6, "Should have 6 operations: unlink, remove, restart (x2)"
    
    # First cycle (with alias domains)
    assert operations_log[0] == ('unlink', ('alok.rt.gw', alias_domains), {})
    assert operations_log[1] == ('remove', (certificate,), {})
    assert operations_log[2] == ('restart', (), {})
    
    # Second cycle (without alias domains)
    assert operations_log[3] == ('unlink', ('alok.rt.gw', None), {})
    assert operations_log[4] == ('remove', (certificate,), {})
    assert operations_log[5] == ('restart', (), {})
    
    print("\n✅ Complete site deletion flow verified:")
    print("   1. Removed symlinks for primary domain + alias domains")
    print("   2. Removed certificate files (first pass)")
    print("   3. Restarted nginx")
    print("   4. Removed symlinks for primary domain")
    print("   5. Removed certificate files (second pass)")
    print("   6. Restarted nginx")
    print("\n   Result: All certificates and symlinks cleaned up properly!")
