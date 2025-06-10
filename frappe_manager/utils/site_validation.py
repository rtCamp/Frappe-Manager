from typing import Optional, List
from frappe_manager.display_manager.DisplayManager import richprint

def validate_site_selection_for_operation(
    bench,
    site_name: Optional[str],
    operation_type: str,  # "safe", "site_specific", "destructive"
    operation_name: str,
    benchname: str
) -> str:
    """
    Validate and handle site selection based on operation type
    Returns the final site_name to use
    """
    
    if len(bench.sites) == 0:
        richprint.exit(f"No sites found in bench {benchname}")
    
    if len(bench.sites) == 1:
        # Single site - always use it
        final_site_name = list(bench.sites.keys())[0]
        if not site_name:
            richprint.print(f"Using site: {final_site_name}")
        return final_site_name
    
    # Multi-site scenario
    if not site_name:
        if operation_type == "safe":
            # Use default site with notification
            default_site = bench.get_default_site()
            richprint.print(f"Using default site: {default_site.name}")
            richprint.print(f"(Use --site <name> to target a different site)")
            return default_site.name
            
        elif operation_type in ["site_specific", "destructive"]:
            # Require explicit selection
            _show_site_selection_help(bench, operation_name, benchname)
            richprint.exit(f"Please specify --site <sitename> for {operation_name}")
    
    # Validate provided site exists
    if site_name not in bench.sites:
        richprint.exit(f"Site {site_name} not found in bench {benchname}")
    
    return site_name

def _show_site_selection_help(bench, operation_name: str, benchname: str):
    """Show helpful site selection information"""
    richprint.print(f"{operation_name} requires site selection. Available sites in {benchname}:")
    default_site = bench.get_default_site()
    
    for site in bench.sites.keys():
        marker = " (default)" if site == default_site.name else ""
        richprint.print(f"  - {site}{marker}")
