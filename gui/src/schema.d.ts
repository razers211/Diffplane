// Generated from openapi.json by scripts/api-types.mjs (npm run api-types): do not edit.
export interface paths {
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Config
         * @description Generated config for one device (`annet gen`). Read-only: nothing is fetched or deployed.
         */
        get: operations["device_config_devices__name__config_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Devices
         * @description Devices from NetBox, optionally for one fabric (site slug).
         */
        get: operations["devices_devices_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/topology": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Topology
         * @description NetBox cabling checked against LLDP (see orchestrator/topology.py).
         */
        get: operations["get_topology_topology_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/interfaces": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Interfaces
         * @description Per interface: oper status, traffic in bits/s (last two samples, physical ports), errors and
         *     discards (last 5 minutes), from the collector's counters in Prometheus.
         */
        get: operations["device_interfaces_devices__name__interfaces_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/overview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Overview
         * @description What only Prometheus knows, for the overview page: alerts firing now and the busiest
         *     physical ports of the fabric's active devices, with 15 minutes of history. Devices, drift and
         *     deploys come from their own endpoints.
         */
        get: operations["overview_overview_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/traffic": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Traffic
         * @description In/out bits per second now on every physical port of the fabric's active devices, for the
         *     topology map's traffic view.
         */
        get: operations["fabric_traffic_traffic_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/control-plane": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Control Plane
         * @description BGP and EVPN peers, OSPF neighbours, M-LAG, VXLAN tunnels, route and MAC counts of one switch,
         *     as the collector last read them (every 30 s).
         */
        get: operations["device_control_plane_devices__name__control_plane_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/hardware": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Hardware
         * @description Optics, fans, power, CPU, memory and temperatures of one switch, as the collector last read them
         *     (each group every 50 s), with problems; an optic's level counts only on a port in use.
         */
        get: operations["device_hardware_devices__name__hardware_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Interface History
         * @description One physical port's traffic over the last `minutes` (15 to 1440), for the device page's chart.
         */
        get: operations["interface_history_devices__name__history_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/diff-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Diff Jobs */
        get: operations["list_diff_jobs_diff_jobs_get"];
        put?: never;
        /**
         * Start Diff Job
         * @description Run `annet diff` on active devices (of a fabric, or listed). Read-only on devices.
         */
        post: operations["start_diff_job_diff_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/diff-jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Diff Job */
        get: operations["get_diff_job_diff_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/diff": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Latest Diff
         * @description The device's most recent finished diff snapshot.
         */
        get: operations["device_latest_diff_devices__name__diff_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/drift": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Drift Status
         * @description Each active device's latest diff (in sync, out of sync, error, unknown) and the schedule.
         */
        get: operations["drift_status_drift_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Login */
        post: operations["login_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/setup": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Setup Needed
         * @description Whether the platform has no account yet (no login needed: the login page asks).
         */
        get: operations["setup_needed_setup_get"];
        put?: never;
        /**
         * First Admin
         * @description The first admin, with the setup code from the orchestrator's log; signs them in.
         */
        post: operations["first_admin_setup_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout */
        post: operations["logout_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Me */
        get: operations["me_auth_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/password": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Change My Password
         * @description Any user changes their own password (the current one is asked again). Signs them out everywhere.
         */
        post: operations["change_my_password_auth_password_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Settings
         * @description Every setting and where its value comes from; secrets never shown.
         */
        get: operations["list_settings_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/settings/{key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Setting */
        put: operations["set_setting_settings__key__put"];
        post?: never;
        /**
         * Reset Setting
         * @description Back to the value in deploy/.env.
         */
        delete: operations["reset_setting_settings__key__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/images": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Images */
        get: operations["list_images_images_get"];
        put?: never;
        /**
         * Upload Image
         * @description The file is the raw request body (no form encoding), streamed to the store while hashed.
         */
        post: operations["upload_image_images_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/images/{image_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Image */
        delete: operations["delete_image_images__image_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/directory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Directory View
         * @description The directory settings (secrets only as set or not) and which providers are complete.
         */
        get: operations["directory_view_directory_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/directory/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Directory Test
         * @description Ask the directory as a sign-in would, without signing in or keeping an account.
         */
        post: operations["directory_test_directory_test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alerts/receiver": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Alert Receiver
         * @description Where alerts go now, and what is missing for the chosen receiver.
         */
        get: operations["alert_receiver_alerts_receiver_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alerts/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Test Alert
         * @description Send a test alert through Alertmanager to the receiver.
         */
        post: operations["test_alert_alerts_test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Users */
        get: operations["list_users_users_get"];
        put?: never;
        /** Create User */
        post: operations["create_user_users_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/users/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete User */
        delete: operations["delete_user_users__name__delete"];
        options?: never;
        head?: never;
        /**
         * Change User
         * @description An admin changes a user's role, the fabrics they are limited to, or sets a new
         *     password (which signs that user out).
         */
        patch: operations["change_user_users__name__patch"];
        trace?: never;
    };
    "/deploy-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Deploy Jobs */
        get: operations["list_deploy_jobs_deploy_jobs_get"];
        put?: never;
        /**
         * Request Deploy
         * @description Compute patches for approval. Nothing is sent to devices until approved.
         */
        post: operations["request_deploy_deploy_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deploy-jobs/{job_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Deploy
         * @description Approve the shown patches and start the rollout now, or in a change window.
         */
        post: operations["approve_deploy_deploy_jobs__job_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deploy-jobs/{job_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cancel Deploy
         * @description Cancel a scheduled deploy, or stop a running one after its wave.
         */
        post: operations["cancel_deploy_deploy_jobs__job_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deploy-jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Deploy Job */
        get: operations["get_deploy_job_deploy_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/upgrade-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Upgrade Jobs */
        get: operations["list_upgrade_jobs_upgrade_jobs_get"];
        put?: never;
        /**
         * Request Upgrade
         * @description Read each switch (display only) and plan its upgrade for approval. Nothing changes until approved.
         */
        post: operations["request_upgrade_upgrade_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/upgrade-jobs/{job_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Upgrade
         * @description Approve the shown plans (deploy rights) and upgrade one switch at a time, now or in a window.
         */
        post: operations["approve_upgrade_upgrade_jobs__job_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/upgrade-jobs/{job_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cancel Upgrade
         * @description Cancel a scheduled upgrade, or stop a running one after its current switch.
         */
        post: operations["cancel_upgrade_upgrade_jobs__job_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/upgrade-jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Upgrade Job */
        get: operations["get_upgrade_job_upgrade_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Audit Trail
         * @description Who did what, newest first. A limited account sees the events of its fabrics: a job
         *     of one of them, or a detail naming one of them or one of their switches. Each version of an
         *     intent document written is an event too (`intent.document`), seen by the fabrics it is
         *     assigned to.
         */
        get: operations["audit_trail_audit_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/change-sets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Change Sets */
        get: operations["list_change_sets_fabrics__site__change_sets_get"];
        put?: never;
        /**
         * Create Change Set
         * @description A new, empty change set: intent writes sent with `X-Change-Set: <id>` are saved in it.
         */
        post: operations["create_change_set_fabrics__site__change_sets_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/change-sets/{change_set_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Change Set */
        get: operations["get_change_set_fabrics__site__change_sets__change_set_id__get"];
        put?: never;
        post?: never;
        /**
         * Discard Change Set
         * @description Throw an open change set away (its author, approvers and admins); nothing was applied.
         */
        delete: operations["discard_change_set_fabrics__site__change_sets__change_set_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/change-sets/{change_set_id}/deploy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Deploy Change Set
         * @description Prepare the change set's deploy: each switch's patch with it applied, to approve for a window.
         *     The change set cannot change any more unless that job ends without applying it.
         */
        post: operations["deploy_change_set_fabrics__site__change_sets__change_set_id__deploy_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/segments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Segments
         * @description Segments of one fabric (site slug), plus unassigned ones from before fabrics.
         */
        get: operations["list_segments_segments_get"];
        put?: never;
        /** Create Segment */
        post: operations["create_segment_segments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/ports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Attachable Ports
         * @description Ports of a leaf that may carry segments (server trunks first); `services`: also those
         *     services sit on (a new service may join them).
         */
        get: operations["attachable_ports_devices__name__ports_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/faceplate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Faceplate
         * @description Every front-panel port and what it carries, for the drawn front panel. Read only.
         */
        get: operations["device_faceplate_devices__name__faceplate_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/segments/next-bd-id": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Next Bd Id */
        get: operations["next_bd_id_segments_next_bd_id_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/segments/{site}/{vni}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Segment */
        delete: operations["delete_segment_segments__site___vni__delete"];
        options?: never;
        head?: never;
        /**
         * Rename Segment
         * @description Rename a segment; a routed one's VPN instance is replaced at its next deploy.
         */
        patch: operations["rename_segment_segments__site___vni__patch"];
        trace?: never;
    };
    "/segments/{site}/{vni}/attachments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Attach Segment */
        post: operations["attach_segment_segments__site___vni__attachments_post"];
        /** Detach Segment */
        delete: operations["detach_segment_segments__site___vni__attachments_delete"];
        options?: never;
        head?: never;
        /** Change Attachment */
        patch: operations["change_attachment_segments__site___vni__attachments_patch"];
        trace?: never;
    };
    "/services": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Services
         * @description Firewall services of one fabric; the first is the default for new routed segments.
         */
        get: operations["list_services_services_get"];
        put?: never;
        /**
         * Create Service
         * @description On an M-LAG pair's server trunk: `address` is the Vlanif subnet, or the shared M-LAG
         *     address with each leaf's address in `addresses`. `vrf`: an existing VPN instance to join
         *     (another service's, or a routed segment's); none: its own.
         */
        post: operations["create_service_services_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/services/{site}/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Service */
        delete: operations["delete_service_services__site___name__delete"];
        options?: never;
        head?: never;
        /** Change Service */
        patch: operations["change_service_services__site___name__patch"];
        trace?: never;
    };
    "/services/{site}/{name}/ipv6": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Service Ipv6
         * @description A service's IPv6: its Vlanif subnet or address and the next hop; null takes it away.
         */
        put: operations["set_service_ipv6_services__site___name__ipv6_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/management": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Activate Device
         * @description Make a device active with its management address (or change that address).
         */
        put: operations["activate_device_devices__name__management_put"];
        post?: never;
        /**
         * Decommission Device
         * @description Back to planned: management address freed, host keys forgotten; the switch is untouched.
         */
        delete: operations["decommission_device_devices__name__management_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/build": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Build Options
         * @description Known switch models and the fabric's spines and leaves with free ports, for adding switches.
         */
        get: operations["build_options_fabrics__site__build_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/switches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Add Switches
         * @description Add several switches at once: leaf pairs, lone leaves, spines and (centralized
         *     fabrics) spine pairs, with their links.
         */
        post: operations["add_switches_fabrics__site__switches_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/address-plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Fabric Address Plan
         * @description For the new-fabric wizard: a proposed split of one private subnet into the pools,
         *     an unused gateway MAC, and the switch models with a known port layout.
         */
        post: operations["fabric_address_plan_fabrics_address_plan_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Fabrics
         * @description Every fabric (a NetBox site with a seeded fabric context), for the GUI's fabric picker.
         */
        get: operations["list_fabrics_fabrics_get"];
        put?: never;
        /**
         * Create Fabric
         * @description A new fabric with its first switches: everything checked before the first write.
         */
        post: operations["create_fabric_fabrics_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/inventory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Inventory
         * @description Switches not yet in a fabric.
         */
        get: operations["list_inventory_inventory_get"];
        put?: never;
        /** Add Inventory */
        post: operations["add_inventory_inventory_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/inventory/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Inventory Models
         * @description Switch models with a known port layout: the only ones the platform can place in a fabric.
         */
        get: operations["inventory_models_inventory_models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/inventory/{serial}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Inventory */
        put: operations["update_inventory_inventory__serial__put"];
        post?: never;
        /** Remove Inventory */
        delete: operations["remove_inventory_inventory__serial__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/spines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Add Spine
         * @description Add a spine (planned) with its links to existing leaves.
         */
        post: operations["add_spine_fabrics__site__spines_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/leaf-pairs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Add Leaf Pair
         * @description Add an M-LAG leaf pair (planned) with its peer-link and uplinks to existing spines.
         */
        post: operations["add_leaf_pair_fabrics__site__leaf_pairs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/removal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Removal Plan
         * @description What removing a planned switch would take with it (its M-LAG peer, links on other switches),
         *     or why it is refused (409).
         */
        get: operations["removal_plan_devices__name__removal_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Remove Device
         * @description Remove a planned switch (a leaf with its M-LAG peer) from NetBox, with its cables and addresses.
         */
        delete: operations["remove_device_devices__name__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/hostkeys/fetch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Fetch Hostkeys
         * @description The host keys the switch offers now (ports 22 and 830), with fingerprints, next to the pins.
         *     Fetching proves nothing: a person compares the fingerprints with the switch's own display.
         */
        post: operations["fetch_hostkeys_devices__name__hostkeys_fetch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/hostkeys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Pin Hostkeys
         * @description Pin the approved keys (approver), if the switch still offers exactly these; then read what the
         *     switch is (model, software, ports), which may not stop the pin.
         */
        put: operations["pin_hostkeys_devices__name__hostkeys_put"];
        post?: never;
        /**
         * Forget Hostkeys
         * @description Forget the pins: Annet and the collector refuse the switch until it is pinned again.
         */
        delete: operations["forget_hostkeys_devices__name__hostkeys_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ztp": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Ztp
         * @description Registered switches and what they reported; the keys are public, nothing here is secret.
         */
        get: operations["list_ztp_ztp_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/ztp": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Register Ztp
         * @description Register a planned switch for zero-touch provisioning.
         */
        put: operations["register_ztp_devices__name__ztp_put"];
        post?: never;
        /** Unregister Ztp */
        delete: operations["unregister_ztp_devices__name__ztp_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/ztp/files": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ztp Files
         * @description The intermediate file and the first-boot config the switch gets, passwords masked.
         */
        get: operations["ztp_files_devices__name__ztp_files_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/ztp/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Ztp
         * @description Approve a provisioned switch's reported keys: pinned, then the switch is activated and read.
         */
        post: operations["approve_ztp_devices__name__ztp_approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/inventory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Switch Inventory State
         * @description The model NetBox has, and the model, release and patch the switch last reported (collector).
         */
        get: operations["switch_inventory_state_devices__name__inventory_get"];
        put?: never;
        /**
         * Read Switch Inventory
         * @description Read the switch's model, software and ports now and bring NetBox in step.
         */
        post: operations["read_switch_inventory_devices__name__inventory_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/racks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Racks
         * @description The fabric's locations (rooms, halls) and racks, with the devices in each rack.
         */
        get: operations["list_racks_fabrics__site__racks_get"];
        put?: never;
        /** Add Rack */
        post: operations["add_rack_fabrics__site__racks_post"];
        /** Delete Rack */
        delete: operations["delete_rack_fabrics__site__racks_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/location": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Place Device */
        put: operations["place_device_devices__name__location_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/role": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Device Role
         * @description Leaf <-> border-leaf, the M-LAG peer with it.
         */
        put: operations["set_device_role_devices__name__role_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/configs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Config Versions
         * @description The switch's kept running configs, newest first (no text), and whether backups are on.
         */
        get: operations["list_config_versions_devices__name__configs_get"];
        put?: never;
        /**
         * Back Up Config
         * @description Back up the switch's running config now (read-only on the switch).
         */
        post: operations["back_up_config_devices__name__configs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/configs/{version_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Show Config Version
         * @description One kept config, secrets masked.
         */
        get: operations["show_config_version_devices__name__configs__version_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/configs/{older}/diff/{newer}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Diff Config Versions
         * @description A unified diff between two kept configs, secrets masked.
         */
        get: operations["diff_config_versions_devices__name__configs__older__diff__newer__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/intent-snapshots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Intent Snapshots
         * @description The fabric's rollback points: its intent at each verified deploy, newest first.
         */
        get: operations["list_intent_snapshots_fabrics__site__intent_snapshots_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/intent-snapshots/{snapshot_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Intent Snapshot */
        get: operations["get_intent_snapshot_fabrics__site__intent_snapshots__snapshot_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/intent-snapshots/{snapshot_id}/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Preview Rollback
         * @description The intent operations that would restore the rollback point, for one switch (`device`)
         *     or the whole fabric; nothing is written.
         */
        get: operations["preview_rollback_fabrics__site__intent_snapshots__snapshot_id__rollback_get"];
        put?: never;
        /**
         * Apply Rollback
         * @description Restore a rollback point into intent: the switches change only through the usual
         *     review, approval and deploy. Stops at the first operation the orchestrator refuses.
         */
        post: operations["apply_rollback_fabrics__site__intent_snapshots__snapshot_id__rollback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Checks
         * @description Pre-deploy checks of the fabric's intent now: errors block approval, warnings inform.
         */
        get: operations["fabric_checks_fabrics__site__checks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Health
         * @description Health scores of the fabric's switches, links and segments, with their reasons; the
         *     fabric's score is its worst switch's or link's.
         */
        get: operations["fabric_health_fabrics__site__health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/capacity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Capacity
         * @description Every port and trunk of the fabric with its peak hourly 95th percentile over the last `days`
         *     (1 to 90) as a share of its speed, and its forecast to 70 %; busiest first.
         */
        get: operations["fabric_capacity_fabrics__site__capacity_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/capacity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Port Capacity
         * @description One port's or trunk's hourly traffic over the last `days` (1 to 395), its daily 95th
         *     percentiles and its forecast.
         */
        get: operations["port_capacity_devices__name__capacity_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Timeline
         * @description What changed, newest first: state changes the event watcher recorded (links, peers,
         *     hardware, alerts, drift, health) and deploys and intent changes from the audit trail (kind
         *     `change`). A limited account sees its fabrics only.
         */
        get: operations["timeline_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/intent": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Export Intent
         * @description The fabric's intent as YAML (orchestrator/intent_file.py), to keep in git.
         */
        get: operations["export_intent_fabrics__site__intent_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/intent/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preview Intent
         * @description What importing the file would change in intent, in order; nothing is written.
         */
        post: operations["preview_intent_fabrics__site__intent_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/intent/import": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Import Intent
         * @description Bring intent to the file's: the operations the preview showed, each through the
         *     orchestrator's own checks, stopping at the first refusal. Switches change only through the
         *     usual review, approval and deploy.
         */
        post: operations["import_intent_fabrics__site__intent_import_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/trunks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Trunks
         * @description A device's Eth-Trunks, its free ports and (on a pair) the ports free on both devices.
         */
        get: operations["list_trunks_devices__name__trunks_get"];
        put?: never;
        /** Create Trunk */
        post: operations["create_trunk_devices__name__trunks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/trunks/{trunk}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Trunk */
        delete: operations["delete_trunk_devices__name__trunks__trunk__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/segments/{site}/{vni}/routing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Routing
         * @description Route a segment (gateway subnet from the fabric pool, firewall default route and/or
         *     peerings) or stop routing it.
         */
        put: operations["set_routing_segments__site___vni__routing_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/segments/{site}/{vni}/relay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Relay
         * @description DHCP relay of a routed segment: up to 8 servers; each gateway leaf relays from its
         *     own loopback (from the fabric's relay pool) with Option 82 link-selection.
         */
        put: operations["set_relay_segments__site___vni__relay_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/segments/{site}/{vni}/ipv6": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Segment Ipv6
         * @description IPv6 of a routed segment: its /64 from the fabric's IPv6 pool, gateway ::1, and how
         *     its hosts get addresses (SLAAC, DHCPv6 relayed to its servers, or none).
         */
        put: operations["set_segment_ipv6_segments__site___vni__ipv6_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/ipv6-pool": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Ipv6 Pool
         * @description The fabric's IPv6 pool: a /64 per segment; its first /64 for relay loopbacks.
         */
        put: operations["set_ipv6_pool_fabrics__site__ipv6_pool_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/pip-pool": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Pip Pool
         * @description Move the fabric's M-LAG pairs to the PIP layout: own PIPs on LoopBack1, VTEPs on LoopBack2.
         */
        put: operations["set_pip_pool_fabrics__site__pip_pool_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/relay-pool": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Relay Pool
         * @description The fabric's relay loopback pool, in its address namespace.
         */
        put: operations["set_relay_pool_fabrics__site__relay_pool_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Architecture
         * @description The fabric's architecture: symmetric, or centralized with its gateway pair; its
         *     NTP servers (set here once the fabric exists); and its other settings, read only.
         */
        get: operations["fabric_architecture_fabrics__site__get"];
        put?: never;
        post?: never;
        /**
         * Delete Fabric
         * @description Delete a fabric from NetBox: every switch planned, no deploy running, its name typed.
         *     Nothing is sent to any switch; the platform's history stays.
         */
        delete: operations["delete_fabric_fabrics__site__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Snmp
         * @description Who may read the fabric's switches: v2c on or off, v3 readers (and whether their passwords are
         *     stored: never the passwords), trap targets.
         */
        get: operations["fabric_snmp_fabrics__site__snmp_get"];
        /**
         * Set Fabric Snmp
         * @description SNMP location and contact of every switch of the fabric: intent, deployed as usual.
         */
        put: operations["set_fabric_snmp_fabrics__site__snmp_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/gateway-mac": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Gateway Mac
         * @description The fabric's anycast gateway MAC: every VBDIF changes at the next deploy.
         */
        put: operations["set_fabric_gateway_mac_fabrics__site__gateway_mac_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/deletion": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Deletion
         * @description What deleting the fabric would remove, and what still prevents it.
         */
        get: operations["fabric_deletion_fabrics__site__deletion_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/usual-window": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Usual Window
         * @description The fabric's usual change window and its openings of the coming week (UTC), offered when scheduling.
         */
        get: operations["get_usual_window_fabrics__site__usual_window_get"];
        /**
         * Set Usual Window
         * @description Set or clear (no body) a fabric's usual change window: approvers and admins.
         *     A platform setting, not intent: no switch changes.
         */
        put: operations["set_usual_window_fabrics__site__usual_window_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/deploys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Deploys
         * @description Allow or stop deploys to one fabric. Allowing asks for the fabric's name, typed.
         */
        put: operations["set_fabric_deploys_fabrics__site__deploys_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/ntp": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Ntp
         * @description Set the fabric's NTP servers (the GUI owns them after the first seed).
         */
        put: operations["set_fabric_ntp_fabrics__site__ntp_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/overrides": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Overrides
         * @description What an imported fabric keeps the way it was found, and what it left unmanaged.
         */
        get: operations["fabric_overrides_fabrics__site__overrides_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/overrides/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preview Override
         * @description The patch "Make it standard" would give; nothing is saved.
         */
        post: operations["preview_override_fabrics__site__overrides_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/overrides/{path}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Override */
        put: operations["set_override_fabrics__site__overrides__path__put"];
        post?: never;
        /** Make Standard */
        delete: operations["make_standard_fabrics__site__overrides__path__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/ospf-auth": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Fabric Ospf Auth
         * @description On or off, the key IDs, which one is sent, and where a roll stands; never a key.
         */
        get: operations["get_fabric_ospf_auth_fabrics__site__ospf_auth_get"];
        /** Set Fabric Ospf Auth */
        put: operations["set_fabric_ospf_auth_fabrics__site__ospf_auth_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/ospf-auth/roll": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Roll Fabric Ospf Key */
        post: operations["roll_fabric_ospf_key_fabrics__site__ospf_auth_roll_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/switch-login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Fabric Switch Login
         * @description On or off, the domain, the servers and whether a key is set; never the key.
         */
        get: operations["get_fabric_switch_login_fabrics__site__switch_login_get"];
        /** Set Fabric Switch Login */
        put: operations["set_fabric_switch_login_fabrics__site__switch_login_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/switch-accounts/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Add Switch Account */
        put: operations["add_switch_account_fabrics__site__switch_accounts__name__put"];
        post?: never;
        /** Remove Switch Account */
        delete: operations["remove_switch_account_fabrics__site__switch_accounts__name__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/netstream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Netstream
         * @description Sampled NetStream for every switch of the fabric: intent, deployed as usual.
         */
        put: operations["set_fabric_netstream_fabrics__site__netstream_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/syslog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Syslog
         * @description Syslog from every switch of the fabric to the platform: intent, deployed as usual.
         */
        put: operations["set_fabric_syslog_fabrics__site__syslog_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp/v2c": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Snmp V2C
         * @description v2c (the platform's community) on or off on every switch of the fabric: intent, deployed as usual.
         */
        put: operations["set_fabric_snmp_v2c_fabrics__site__snmp_v2c_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp/readers/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Snmp Reader
         * @description A v3 reader (a USM user of group FABRIC-RO): intent; its passwords are typed by a password job.
         */
        put: operations["set_fabric_snmp_reader_fabrics__site__snmp_readers__name__put"];
        post?: never;
        /** Delete Fabric Snmp Reader */
        delete: operations["delete_fabric_snmp_reader_fabrics__site__snmp_readers__name__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp/readers/{name}/passwords": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Fabric Snmp Passwords
         * @description Store a reader's passwords (write-only; typed on the switches by the next password job).
         */
        put: operations["set_fabric_snmp_passwords_fabrics__site__snmp_readers__name__passwords_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp/traps/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Fabric Snmp Trap */
        put: operations["set_fabric_snmp_trap_fabrics__site__snmp_traps__name__put"];
        post?: never;
        /** Delete Fabric Snmp Trap */
        delete: operations["delete_fabric_snmp_trap_fabrics__site__snmp_traps__name__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp/copy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Copy Fabric Snmp
         * @description Readers, trap targets and stored passwords of another fabric, added to this one's.
         */
        post: operations["copy_fabric_snmp_fabrics__site__snmp_copy_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/snmp/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Snmp State
         * @description Each reader on each active switch, read now from the switches.
         */
        get: operations["fabric_snmp_state_fabrics__site__snmp_state_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/snmp-password-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Snmp Password Jobs */
        get: operations["list_snmp_password_jobs_snmp_password_jobs_get"];
        put?: never;
        /**
         * Request Snmp Passwords
         * @description Type readers' stored passwords on every active switch of the fabric, after approval.
         */
        post: operations["request_snmp_passwords_snmp_password_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/snmp-password-jobs/{job_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Snmp Passwords */
        post: operations["approve_snmp_passwords_snmp_password_jobs__job_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/snmp-password-jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Snmp Password Job */
        get: operations["get_snmp_password_job_snmp_password_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Microsegmentation
         * @description Mode, EPGs (every routed segment's, named groups inside segments), contracts and the most
         *     a switch would hold of each resource.
         */
        get: operations["get_microsegmentation_fabrics__site__microsegmentation_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/microsegmentation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Microsegmentation
         * @description Read now from the switch: TCAM use per chip, each contract's counters (classifier
         *     `GBP-<from>-<to>`), and rules or members it could not install. Read only, nothing stored.
         */
        get: operations["device_microsegmentation_devices__name__microsegmentation_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation/mode": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Microsegmentation Mode
         * @description Off, Learn or Enforce. Turning it on is refused while an active switch that would get it
         *     does not announce the huawei-microsegmentation module (V300R025 or later).
         */
        put: operations["set_microsegmentation_mode_fabrics__site__microsegmentation_mode_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation/groups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Microseg Group */
        post: operations["create_microseg_group_fabrics__site__microsegmentation_groups_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation/groups/{epg}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Microseg Group */
        put: operations["update_microseg_group_fabrics__site__microsegmentation_groups__epg__put"];
        post?: never;
        /** Delete Microseg Group */
        delete: operations["delete_microseg_group_fabrics__site__microsegmentation_groups__epg__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation/epgs/{epg}/inside": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Microseg Inside */
        put: operations["set_microseg_inside_fabrics__site__microsegmentation_epgs__epg__inside_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation/contracts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Microseg Contract
         * @description Allow one EPG to another for these services (replaces the contract between them); the
         *     reply rules are generated.
         */
        put: operations["set_microseg_contract_fabrics__site__microsegmentation_contracts_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/microsegmentation/contracts/{source}/{destination}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Microseg Contract */
        delete: operations["delete_microseg_contract_fabrics__site__microsegmentation_contracts__source___destination__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/qos": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Qos
         * @description On or off, the segment and port marks, and the queue plan every port gets while on.
         */
        get: operations["get_qos_fabrics__site__qos_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/qos/enabled": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Qos Enabled */
        put: operations["set_qos_enabled_fabrics__site__qos_enabled_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/qos/segments/{vni}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Qos Segment */
        put: operations["set_qos_segment_fabrics__site__qos_segments__vni__put"];
        post?: never;
        /** Delete Qos Segment */
        delete: operations["delete_qos_segment_fabrics__site__qos_segments__vni__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/qos/ports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Qos Port
         * @description Mark everything entering a port or Eth-Trunk (an M-LAG trunk on both leaves); mark null removes it.
         */
        put: operations["set_qos_port_fabrics__site__qos_ports_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/port-settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Port Settings */
        get: operations["get_port_settings_fabrics__site__port_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/port-settings/adoption": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Port Settings Adoption
         * @description What turning port settings on would adopt from the switches (read now; nothing written).
         */
        get: operations["port_settings_adoption_fabrics__site__port_settings_adoption_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/port-settings/enabled": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Port Settings Enabled
         * @description On: adopt what the switches have, then manage; off: stop managing (nothing removed).
         */
        put: operations["set_port_settings_enabled_fabrics__site__port_settings_enabled_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/port-settings/{port}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Port Setting */
        get: operations["get_port_setting_devices__name__port_settings__port__get"];
        /**
         * Set Port Setting
         * @description Name, on/off, frame size and (copper) speed of a server port or trunk (an M-LAG trunk on both switches).
         */
        put: operations["set_port_setting_devices__name__port_settings__port__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/protection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Protection
         * @description Off, Watch or On, the protected ports, what each template does, and the ports protection acts
         *     on now (`tripped`, from the collector's last control poll; empty without Prometheus).
         */
        get: operations["get_protection_fabrics__site__protection_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/protection/mode": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Protection Mode */
        put: operations["set_protection_mode_fabrics__site__protection_mode_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/protection/{port}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Port Protection */
        get: operations["get_port_protection_devices__name__protection__port__get"];
        /**
         * Set Port Protection
         * @description Protect a server port or trunk with a template (an M-LAG trunk on both switches); null removes it.
         */
        put: operations["set_port_protection_devices__name__protection__port__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/dci": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Dci */
        get: operations["get_dci_dci_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/dci/links/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Dci Link */
        put: operations["set_dci_link_dci_links__name__put"];
        post?: never;
        /** Delete Dci Link */
        delete: operations["delete_dci_link_dci_links__name__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/dci/links/{name}/segments/{segment}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Dci Segment */
        put: operations["set_dci_segment_dci_links__name__segments__segment__put"];
        post?: never;
        /** Delete Dci Segment */
        delete: operations["delete_dci_segment_dci_links__name__segments__segment__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Routing Intent
         * @description A fabric's routing intent per device, and what is retired (still to be removed).
         */
        get: operations["get_routing_intent_routing_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/targets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Routing Targets
         * @description Where routing intent is set in a fabric: each unpaired device, each M-LAG pair and each
         *     switch of a pair on its own (`pair` names its pair), with its devices and the VPN instances
         *     its routing objects may use (besides the global table).
         */
        get: operations["routing_targets_routing__site__targets_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/devices/{device}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Device Routing
         * @description Replace one device's routing intent; what it no longer has is retired (removed by the
         *     next deploy). The body is validated: only forms verified on the lab switch are accepted.
         */
        put: operations["set_device_routing_routing__site__devices__device__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/targets/{target}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Save Target Routing
         * @description Replace a target's routing intent and change the fabric's library (fabric-wide prefix
         *     lists and route-policies, any target may use them) in one write.
         */
        put: operations["save_target_routing_routing__site__targets__target__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/library/promote": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Promote Routing Library
         * @description Move the lists and policies every target has identically, and uses, into the fabric's
         *     library; nothing changes on a switch. `apply` false: preview.
         */
        post: operations["promote_routing_library_routing__site__library_promote_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/library": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Routing Library
         * @description The fabric's library and, for each object, the targets whose routing uses it.
         */
        get: operations["routing_library_routing__site__library_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Routing Imports
         * @description The latest auto-import per device of the fabric: what was adopted, what is unmanaged.
         */
        get: operations["routing_imports_routing__site__imports_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/routing/{site}/devices/{device}/import": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Import Routing
         * @description Read the switch now and adopt its hand-made routing objects (also runs after each drift
         *     run and before each deploy). Read-only on the switch.
         */
        post: operations["import_routing_routing__site__devices__device__import_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/flows": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Flows
         * @description Top talkers, conversations, segment pairs and busiest ports of the fabric's NetStream
         *     samples over the last `minutes`: edge ports by default, `kind=vxlan` for uplinks.
         */
        get: operations["fabric_flows_fabrics__site__flows_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/flows": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Flows
         * @description The same for one switch's own ports.
         */
        get: operations["device_flows_devices__name__flows_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Logs
         * @description A switch's syslog messages, newest first: at `severity` or worse (0 emergency ...
         *     7 debugging), containing `text`, over the last `hours` (kept 90 days).
         */
        get: operations["device_logs_devices__name__logs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Fabric Logs
         * @description Every switch's syslog messages of a fabric, newest first (the NOC's feed): warnings and
         *     worse unless asked otherwise.
         */
        get: operations["fabric_logs_fabrics__site__logs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/trace": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Path Trace
         * @description The path from `source` to `destination` (IPv4 or MAC) in the fabric: ARP and MAC tables
         *     of its active switches read now, the rest from intent. Nothing is stored or configured.
         */
        get: operations["path_trace_fabrics__site__trace_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/reach": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Reachability
         * @description Why can't A reach B: the first place traffic stops, or the path both ways. Nothing is
         *     stored or configured.
         */
        get: operations["reachability_fabrics__site__reach_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/fabrics/{site}/reach/hosts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Reach Hosts
         * @description What the Reachability boxes suggest, from the tables read now.
         */
        get: operations["reach_hosts_fabrics__site__reach_hosts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/routes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Routes
         * @description Every instance's IPv4 and IPv6 routing tables, read from the switch now (inactive routes included).
         */
        get: operations["device_routes_devices__name__routes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/bgp": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Bgp
         * @description The BGP tables and peers (IPv4 and IPv6) of the global table or a VPN instance, read from the switch now.
         */
        get: operations["device_bgp_devices__name__bgp_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{name}/bgp/simulation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Device Bgp Simulation
         * @description One peer's routes with and without its policies (simulated; the switch is not changed).
         */
        get: operations["device_bgp_simulation_devices__name__bgp_simulation_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start Import
         * @description Start an import: the host keys each address offers (ports 22 and 830), to compare.
         */
        post: operations["start_import_imports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{import_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Import */
        get: operations["get_import_imports__import_id__get"];
        put?: never;
        post?: never;
        /** Drop Import */
        delete: operations["drop_import_imports__import_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{import_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Import Keys
         * @description An approver compared the fingerprints with the switch's console: these keys are trusted.
         */
        post: operations["approve_import_keys_imports__import_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{import_id}/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Read Import
         * @description Read every approved switch over SSH (display commands only).
         */
        post: operations["read_import_imports__import_id__read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{import_id}/detect": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Detect Import
         * @description New fabric and Add switches: read each approved switch's model and ports over NETCONF.
         */
        post: operations["detect_import_imports__import_id__detect_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{import_id}/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preview Import
         * @description The fabric the switches give, and the patch each would get; nothing is written.
         */
        post: operations["preview_import_imports__import_id__preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{import_id}/write": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Write Import
         * @description Write the imported fabric to NetBox (its switches active and pinned; deploys stay off).
         */
        post: operations["write_import_imports__import_id__write_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Activation */
        Activation: {
            /** Address */
            address: string;
            /**
             * Interface
             * @default MEth0/0/0
             */
            interface: string;
        };
        /** AddressPlanRequest */
        AddressPlanRequest: {
            /** Supernet */
            supernet: string;
        };
        /** Aggregate */
        Aggregate: {
            /** Vpn */
            vpn?: string | null;
            /** Prefix */
            prefix: string;
            /**
             * Detail Suppressed
             * @default false
             */
            detail_suppressed: boolean;
            /** Suppress Policy */
            suppress_policy?: string | null;
        };
        /** ApprovedKeys */
        ApprovedKeys: {
            /** Keys */
            keys: string[];
        };
        /** AsPathRule */
        AsPathRule: {
            /** Index */
            index: number;
            /**
             * Action
             * @enum {string}
             */
            action: "permit" | "deny";
            /** Regex */
            regex: string;
        };
        /** Attachment */
        Attachment: {
            /** Device */
            device: string;
            /** Interface */
            interface: string;
            /** Vid */
            vid: number | null;
        };
        /** BgpPeer */
        BgpPeer: {
            /** Vpn */
            vpn?: string | null;
            /** Address */
            address: string;
            /** Asn */
            asn: number;
            /** Import Policy */
            import_policy?: string | null;
            /** Export Policy */
            export_policy?: string | null;
            /** Import Prefix List */
            import_prefix_list?: string | null;
            /** Export Prefix List */
            export_prefix_list?: string | null;
        };
        /** Body_approve_deploy_deploy_jobs__job_id__approve_post */
        Body_approve_deploy_deploy_jobs__job_id__approve_post: {
            /** Window Start */
            window_start?: string | null;
            /** Window End */
            window_end?: string | null;
        };
        /** Body_approve_upgrade_upgrade_jobs__job_id__approve_post */
        Body_approve_upgrade_upgrade_jobs__job_id__approve_post: {
            /** Window Start */
            window_start?: string | null;
            /** Window End */
            window_end?: string | null;
        };
        /** Body_attach_segment_segments__site___vni__attachments_post */
        Body_attach_segment_segments__site___vni__attachments_post: {
            /** Device */
            device: string;
            /** Interface */
            interface: string;
            /** Vid */
            vid?: number | null;
        };
        /** Body_change_attachment_segments__site___vni__attachments_patch */
        Body_change_attachment_segments__site___vni__attachments_patch: {
            /** Device */
            device: string;
            /** Interface */
            interface: string;
            /** Vid */
            vid?: number | null;
        };
        /** Body_change_my_password_auth_password_post */
        Body_change_my_password_auth_password_post: {
            /** Current */
            current: string;
            /** New */
            new: string;
        };
        /** Body_copy_fabric_snmp_fabrics__site__snmp_copy_post */
        Body_copy_fabric_snmp_fabrics__site__snmp_copy_post: {
            /** Source */
            source: string;
        };
        /** Body_create_segment_segments_post */
        Body_create_segment_segments_post: {
            /** Site */
            site: string;
            /** Name */
            name: string;
            /** Vni */
            vni: number;
            /** Bd Id */
            bd_id: number;
        };
        /** Body_create_service_services_post */
        Body_create_service_services_post: {
            /** Site */
            site: string;
            /** Name */
            name: string;
            /** Device */
            device: string;
            /** Port */
            port: string;
            /** Vlan */
            vlan: number;
            /** Address */
            address: string;
            /** Next Hop */
            next_hop: string;
            /** Asn */
            asn?: number | null;
            /**
             * Kind
             * @default firewall
             */
            kind: string;
            /** Addresses */
            addresses?: {
                [key: string]: string;
            } | null;
            /** Ipv6 */
            ipv6?: {
                [key: string]: unknown;
            } | null;
            /** Vrf */
            vrf?: string | null;
        };
        /** Body_delete_fabric_fabrics__site__delete */
        Body_delete_fabric_fabrics__site__delete: {
            /** Confirm */
            confirm: string;
        };
        /** Body_first_admin_setup_post */
        Body_first_admin_setup_post: {
            /** Code */
            code: string;
            /** Username */
            username: string;
            /** Password */
            password: string;
        };
        /** Body_login_auth_login_post */
        Body_login_auth_login_post: {
            /** Username */
            username: string;
            /** Password */
            password: string;
        };
        /** Body_rename_segment_segments__site___vni__patch */
        Body_rename_segment_segments__site___vni__patch: {
            /** Name */
            name: string;
        };
        /** Body_request_deploy_deploy_jobs_post */
        Body_request_deploy_deploy_jobs_post: {
            /** Site */
            site?: string | null;
            /** Devices */
            devices?: string[] | null;
            /** Generators */
            generators?: string[] | null;
        };
        /** Body_request_snmp_passwords_snmp_password_jobs_post */
        Body_request_snmp_passwords_snmp_password_jobs_post: {
            /** Site */
            site: string;
            /** Readers */
            readers: string[];
        };
        /** Body_request_upgrade_upgrade_jobs_post */
        Body_request_upgrade_upgrade_jobs_post: {
            /** Devices */
            devices: string[];
            /** System */
            system?: number | null;
            /** Patch */
            patch?: number | null;
        };
        /** Body_set_fabric_deploys_fabrics__site__deploys_put */
        Body_set_fabric_deploys_fabrics__site__deploys_put: {
            /** Allowed */
            allowed: boolean;
            /**
             * Confirm
             * @default
             */
            confirm: string;
        };
        /** Body_set_fabric_gateway_mac_fabrics__site__gateway_mac_put */
        Body_set_fabric_gateway_mac_fabrics__site__gateway_mac_put: {
            /** Mac */
            mac: string;
        };
        /** Body_set_fabric_snmp_fabrics__site__snmp_put */
        Body_set_fabric_snmp_fabrics__site__snmp_put: {
            /**
             * Location
             * @default
             */
            location: string;
            /**
             * Contact
             * @default
             */
            contact: string;
        };
        /** Body_set_fabric_snmp_v2c_fabrics__site__snmp_v2c_put */
        Body_set_fabric_snmp_v2c_fabrics__site__snmp_v2c_put: {
            /** On */
            on: boolean;
        };
        /** Body_set_ipv6_pool_fabrics__site__ipv6_pool_put */
        Body_set_ipv6_pool_fabrics__site__ipv6_pool_put: {
            /** Prefix */
            prefix: string;
        };
        /** Body_set_pip_pool_fabrics__site__pip_pool_put */
        Body_set_pip_pool_fabrics__site__pip_pool_put: {
            /** Prefix */
            prefix: string;
        };
        /** Body_set_relay_pool_fabrics__site__relay_pool_put */
        Body_set_relay_pool_fabrics__site__relay_pool_put: {
            /** Prefix */
            prefix: string;
        };
        /** Body_set_setting_settings__key__put */
        Body_set_setting_settings__key__put: {
            /** Value */
            value: string;
        };
        /** Body_start_diff_job_diff_jobs_post */
        Body_start_diff_job_diff_jobs_post: {
            /** Site */
            site?: string | null;
            /** Devices */
            devices?: string[] | null;
        };
        /** CommunityRule */
        CommunityRule: {
            /** Index */
            index: number;
            /**
             * Action
             * @enum {string}
             */
            action: "permit" | "deny";
            /** Community */
            community: string;
        };
        /** DciLink */
        DciLink: {
            /** Sides */
            sides: components["schemas"]["DciSide"][];
            /**
             * Split Group
             * @default DCI
             */
            split_group: string;
        };
        /** DciSegment */
        DciSegment: {
            /** Vnis */
            vnis: {
                [key: string]: number;
            };
        };
        /** DciSide */
        DciSide: {
            /** Fabric */
            fabric: string;
            /** Pair */
            pair: string[];
            /**
             * Uplinks
             * @default {}
             */
            uplinks: {
                [key: string]: components["schemas"]["DciUplink"][];
            };
        };
        /** DciUplink */
        DciUplink: {
            /** Port */
            port: string;
            /** Address */
            address: string;
            /** Neighbour */
            neighbour: string;
            /** Asn */
            asn: number;
        };
        /** DeployJob */
        DeployJob: {
            /** Id */
            id: number;
            /** Kind */
            kind: string;
            /** Status */
            status: string;
            /** Site */
            site: string | null;
            /** Requested By */
            requested_by: string | null;
            /** Approved By */
            approved_by: string | null;
            /** Approved At */
            approved_at: string | null;
            /** Generators */
            generators: string[] | null;
            /** Created At */
            created_at: string;
            /** Finished At */
            finished_at: string | null;
            /** Error */
            error: string | null;
            /** Window Start */
            window_start: string | null;
            /** Window End */
            window_end: string | null;
            /** Checks */
            checks: {
                [key: string]: unknown;
            } | null;
            /** Params */
            params: {
                [key: string]: unknown;
            } | null;
            /** Steps */
            steps: components["schemas"]["DeployStep"][];
            /** Log */
            log?: components["schemas"]["LogLine"][];
        };
        /** DeployStep */
        DeployStep: {
            /** Device */
            device: string;
            /** Status */
            status: string;
            /** Patch */
            patch: string | null;
            /** Error */
            error: string | null;
            /** Started At */
            started_at: string | null;
            /** Finished At */
            finished_at: string | null;
        };
        /** Device */
        Device: {
            /** Name */
            name: string;
            /** Site */
            site: string | null;
            /** Role */
            role: string | null;
            /** Status */
            status: string | null;
            /** Primary Ip */
            primary_ip: string | null;
            /** Location */
            location: string | null;
            /** Rack */
            rack: string | null;
            /** Position */
            position: number | null;
            /** Face */
            face: ("front" | "rear") | null;
            /** Pinned */
            pinned: boolean;
            /** Meth Mac */
            meth_mac: string | null;
            /** Peer */
            peer: string | null;
        };
        /** DeviceRouting */
        DeviceRouting: {
            /** Prefix Lists */
            prefix_lists?: {
                [key: string]: components["schemas"]["PrefixRule"][];
            };
            /** As Path Filters */
            as_path_filters?: {
                [key: string]: components["schemas"]["AsPathRule"][];
            };
            /** Community Filters */
            community_filters?: {
                [key: string]: components["schemas"]["CommunityRule"][];
            };
            /** Route Policies */
            route_policies?: {
                [key: string]: components["schemas"]["PolicyNode"][];
            };
            /** Static Routes */
            static_routes?: components["schemas"]["StaticRoute"][];
            /** Bgp Peers */
            bgp_peers?: components["schemas"]["BgpPeer"][];
            /** Aggregates */
            aggregates?: components["schemas"]["Aggregate"][];
            /** Ospf */
            ospf?: components["schemas"]["OspfProcess"][];
        };
        /** DiffJob */
        DiffJob: {
            /** Id */
            id: number;
            /** Kind */
            kind: string;
            /** Requested By */
            requested_by: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "running" | "done" | "failed";
            /** Site */
            site: string | null;
            /** Created At */
            created_at: string;
            /** Finished At */
            finished_at: string | null;
            /** Error */
            error: string | null;
            /** Devices */
            devices: number;
            /** Changed */
            changed: number;
            /** Errors */
            errors: number;
            /** Snapshots */
            snapshots?: components["schemas"]["DiffSnapshot"][];
        };
        /** DiffSnapshot */
        DiffSnapshot: {
            /** Device */
            device: string;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "ok" | "error";
            /** Has Changes */
            has_changes: boolean | null;
            /** Diff */
            diff: string | null;
            /** Error */
            error: string | null;
            /** Created At */
            created_at: string;
            /** Finished At */
            finished_at: string | null;
            /** Job Id */
            job_id: number;
            /** Job Kind */
            job_kind: string | null;
        };
        /** DirectoryTest */
        DirectoryTest: {
            /** Username */
            username: string;
            /** Password */
            password: string;
        };
        /** Drift */
        Drift: {
            scheduler: components["schemas"]["DriftScheduler"] | null;
            /** Devices */
            devices: components["schemas"]["DriftDevice"][];
        };
        /** DriftDevice */
        DriftDevice: {
            /** Device */
            device: string;
            /** Site */
            site: string | null;
            /**
             * State
             * @enum {string}
             */
            state: "in_sync" | "out_of_sync" | "error" | "unknown";
            /** Checked At */
            checked_at: string | null;
            /** Job Id */
            job_id: number | null;
            /** Job Kind */
            job_kind: string | null;
            /** Diff */
            diff: string | null;
            /** Error */
            error: string | null;
        };
        /** DriftScheduler */
        DriftScheduler: {
            /** Interval Seconds */
            interval_seconds: number;
            /** Last Started */
            last_started: number | null;
            /** Last Job Id */
            last_job_id: number | null;
            /** Last Skip */
            last_skip: string | null;
            /** Next Run */
            next_run: number | null;
        };
        /** Fabric */
        Fabric: {
            /** Site */
            site: string;
            /** Name */
            name: string;
            /**
             * Architecture
             * @enum {string}
             */
            architecture: "symmetric" | "centralized";
        };
        /** Failed */
        Failed: {
            /** Text */
            text: string;
            /** Error */
            error: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** Health */
        Health: {
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "error";
            /** Netbox */
            netbox: {
                [key: string]: unknown;
            };
        };
        /** ImportApproval */
        ImportApproval: {
            /** Address */
            address: string;
            /** Keys */
            keys: string[];
        };
        /** ImportChoices */
        ImportChoices: {
            /** Name */
            name: string;
            /**
             * Pools
             * @default {}
             */
            pools: {
                [key: string]: string;
            };
            /** Gateway Mac */
            gateway_mac?: string | null;
            /**
             * Roles
             * @default {}
             */
            roles: {
                [key: string]: string;
            };
            /** Architecture */
            architecture?: string | null;
            /** Gateway Pair */
            gateway_pair?: string[] | null;
            /**
             * Drop
             * @default []
             */
            drop: number[];
        };
        /** ImportStart */
        ImportStart: {
            /** Addresses */
            addresses: string[];
        };
        /** IntentImport */
        IntentImport: {
            /** Yaml */
            yaml: string;
            /** Expected */
            expected: number;
        };
        /** IntentImported */
        IntentImported: {
            /** Fabric */
            fabric: string;
            /** Operations */
            operations: components["schemas"]["Operation"][];
            /** Notes */
            notes: string[];
            /** Ignored */
            ignored: string[];
            /** Done */
            done: string[];
            failed: components["schemas"]["Failed"] | null;
            /** Remaining */
            remaining: string[];
        };
        /** IntentPlan */
        IntentPlan: {
            /** Fabric */
            fabric: string;
            /** Operations */
            operations: components["schemas"]["Operation"][];
            /** Notes */
            notes: string[];
            /** Ignored */
            ignored: string[];
        };
        /** IntentText */
        IntentText: {
            /** Yaml */
            yaml: string;
        };
        /** InventorySwitch */
        InventorySwitch: {
            /** Serial */
            serial: string;
            /** Model */
            model: string;
            /** Mac */
            mac?: string | null;
        };
        /** Ipv6Intent */
        Ipv6Intent: {
            /** Enabled */
            enabled: boolean;
            /**
             * Hosts
             * @default slaac
             */
            hosts: string;
            /**
             * Servers
             * @default []
             */
            servers: components["schemas"]["RelayServer-Input"][];
        };
        /** JobId */
        JobId: {
            /** Id */
            id: number;
        };
        /**
         * LibraryChange
         * @description Library objects to set (or delete: null), by name.
         */
        LibraryChange: {
            /** Prefix Lists */
            prefix_lists?: {
                [key: string]: components["schemas"]["PrefixRule"][] | null;
            };
            /** Route Policies */
            route_policies?: {
                [key: string]: components["schemas"]["PolicyNode"][] | null;
            };
        };
        /** LogLine */
        LogLine: {
            /** At */
            at: string;
            /** Device */
            device: string | null;
            /** Level */
            level: string;
            /** Message */
            message: string;
        };
        /** Me */
        Me: {
            /** Username */
            username: string;
            /**
             * Role
             * @enum {string}
             */
            role: "viewer" | "operator" | "approver" | "admin";
            /** Fabrics */
            fabrics: {
                [key: string]: boolean;
            } | null;
            /** Source */
            source: string;
        };
        /** MicrosegContract */
        MicrosegContract: {
            /** From */
            from: number;
            /** To */
            to: number;
            /** Services */
            services: {
                [key: string]: unknown;
            }[];
        };
        /** MicrosegGroup */
        MicrosegGroup: {
            /** Segment */
            segment: number;
            /** Name */
            name: string;
            /**
             * Prefixes
             * @default []
             */
            prefixes: string[];
            /**
             * Macs
             * @default []
             */
            macs: string[];
            /** Inside */
            inside?: string | null;
        };
        /** MicrosegInside */
        MicrosegInside: {
            /** Inside */
            inside: string;
        };
        /** MicrosegMode */
        MicrosegMode: {
            /** Mode */
            mode: string;
        };
        /** NetStreamSettings */
        NetStreamSettings: {
            /** Enabled */
            enabled: boolean;
            /** Host */
            host?: string | null;
            /** Port */
            port?: number | null;
        };
        /** NewFabric */
        NewFabric: {
            settings: components["schemas"]["NewFabricSettings"];
            switches: components["schemas"]["NewSwitches"];
        };
        /** NewFabricSettings */
        NewFabricSettings: {
            /** Name */
            name: string;
            /** Asn */
            asn: number;
            /**
             * Architecture
             * @default symmetric
             */
            architecture: string;
            /** Prefixes */
            prefixes: {
                [key: string]: string;
            };
            /**
             * Bypass Vlan
             * @default 4093
             */
            bypass_vlan: number;
            /** Gateway Mac */
            gateway_mac: string;
            /**
             * Ntp Servers
             * @default []
             */
            ntp_servers: string[];
            /** Ntp Vpn Instance */
            ntp_vpn_instance?: string | null;
            /** Snmp Location */
            snmp_location?: string | null;
            /** Snmp Contact */
            snmp_contact?: string | null;
        };
        /** NewLeafPair */
        NewLeafPair: {
            /** Leaves */
            leaves: string[];
            /** Model */
            model: string;
            /**
             * Role
             * @default leaf
             */
            role: string;
            /**
             * Peer Link Trunk
             * @default 0
             */
            peer_link_trunk: number;
            /** Peer Link Members */
            peer_link_members: string[];
            /**
             * Uplinks
             * @default []
             */
            uplinks: {
                [key: string]: string;
            }[];
        };
        /** NewLink */
        NewLink: {
            /** Spine */
            spine: string;
            /** Spine Port */
            spine_port: string;
            /** Leaf */
            leaf: string;
            /** Leaf Port */
            leaf_port: string;
        };
        /** NewPair */
        NewPair: {
            /** Leaves */
            leaves: string[];
            /** Model */
            model: string;
            /**
             * Role
             * @default leaf
             */
            role: string;
            /**
             * Peer Link Trunk
             * @default 0
             */
            peer_link_trunk: number;
            /** Peer Link Members */
            peer_link_members: string[];
            /**
             * Management
             * @default {}
             */
            management: {
                [key: string]: string;
            };
            /**
             * Inventory
             * @default {}
             */
            inventory: {
                [key: string]: string;
            };
            /**
             * Gateway
             * @default false
             */
            gateway: boolean;
        };
        /** NewRack */
        NewRack: {
            /** Location */
            location: string;
            /** Rack */
            rack: string;
            /**
             * U Height
             * @default 42
             */
            u_height: number;
        };
        /** NewSpine */
        NewSpine: {
            /** Name */
            name: string;
            /** Model */
            model: string;
            /**
             * Links
             * @default []
             */
            links: {
                [key: string]: string;
            }[];
        };
        /** NewSpinePair */
        NewSpinePair: {
            /** Spines */
            spines: string[];
            /** Model */
            model: string;
            /**
             * Peer Link Trunk
             * @default 0
             */
            peer_link_trunk: number;
            /** Peer Link Members */
            peer_link_members: string[];
            /**
             * Management
             * @default {}
             */
            management: {
                [key: string]: string;
            };
            /**
             * Inventory
             * @default {}
             */
            inventory: {
                [key: string]: string;
            };
            /**
             * Gateway
             * @default false
             */
            gateway: boolean;
        };
        /** NewSwitch */
        NewSwitch: {
            /** Name */
            name: string;
            /** Model */
            model: string;
            /**
             * Role
             * @default spine
             */
            role: string;
            /** Management */
            management?: string | null;
            /** Inventory */
            inventory?: string | null;
        };
        /** NewSwitches */
        NewSwitches: {
            /**
             * Spines
             * @default []
             */
            spines: components["schemas"]["NewSwitch"][];
            /**
             * Pairs
             * @default []
             */
            pairs: components["schemas"]["NewPair"][];
            /**
             * Leaves
             * @default []
             */
            leaves: components["schemas"]["NewSwitch"][];
            /**
             * Spine Pairs
             * @default []
             */
            spine_pairs: components["schemas"]["NewSpinePair"][];
            /**
             * Links
             * @default []
             */
            links: components["schemas"]["NewLink"][];
            /** Detect */
            detect?: string | null;
        };
        /** NewTrunk */
        NewTrunk: {
            /** Number */
            number: number;
            /** Members */
            members: string[];
            /**
             * Mlag
             * @default false
             */
            mlag: boolean;
        };
        /** NewUser */
        NewUser: {
            /** Username */
            username: string;
            /** Password */
            password: string;
            /**
             * Role
             * @default viewer
             */
            role: string;
        };
        /** NtpSettings */
        NtpSettings: {
            /** Servers */
            servers: string[];
            /** Vpn Instance */
            vpn_instance?: string | null;
        };
        /** Operation */
        Operation: {
            /** Kind */
            kind: string;
            /** Text */
            text: string;
        } & {
            [key: string]: unknown;
        };
        /** OspfAuthRoll */
        OspfAuthRoll: {
            /** Action */
            action: string;
        };
        /** OspfAuthSetting */
        OspfAuthSetting: {
            /** Enabled */
            enabled: boolean;
        };
        /** OspfProcess */
        OspfProcess: {
            /** Process */
            process: number;
            /** Vpn */
            vpn: string;
            /**
             * Router Id
             * Format: ipv4
             */
            router_id: string;
            /** Areas */
            areas?: string[];
        };
        /** OverridePath */
        OverridePath: {
            /** Path */
            path: string;
        };
        /** OverrideValue */
        OverrideValue: {
            /** Value */
            value?: unknown;
        };
        /** Placement */
        Placement: {
            /** Location */
            location?: string | null;
            /** Rack */
            rack?: string | null;
            /** Position */
            position?: number | null;
            /** Face */
            face?: string | null;
        };
        /** PolicyNode */
        PolicyNode: {
            /** Node */
            node: number;
            /**
             * Action
             * @enum {string}
             */
            action: "permit" | "deny";
            /** Match Ip Prefix */
            match_ip_prefix?: string | null;
            /** Apply Local Preference */
            apply_local_preference?: number | null;
        };
        /** PortSettings */
        PortSettings: {
            /** Description */
            description?: string | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Frame */
            frame?: number | null;
            /** Speed */
            speed?: number | null;
        };
        /** PortSettingsEnabled */
        PortSettingsEnabled: {
            /** Enabled */
            enabled: boolean;
        };
        /** PrefixRule */
        PrefixRule: {
            /** Index */
            index: number;
            /**
             * Action
             * @enum {string}
             */
            action: "permit" | "deny";
            /** Prefix */
            prefix: string;
            /** Ge */
            ge?: number | null;
            /** Le */
            le?: number | null;
        };
        /** ProtectionMode */
        ProtectionMode: {
            /** Mode */
            mode: string;
        };
        /** ProtectionTemplate */
        ProtectionTemplate: {
            /** Template */
            template?: string | null;
        };
        /** QosEnabled */
        QosEnabled: {
            /** Enabled */
            enabled: boolean;
        };
        /** QosMark */
        QosMark: {
            /** Mark */
            mark: string;
        };
        /** QosPortMark */
        QosPortMark: {
            /** Device */
            device: string;
            /** Port */
            port: string;
            /** Mark */
            mark?: string | null;
        };
        /** Relay */
        Relay: {
            /** Servers */
            servers: components["schemas"]["RelayServer-Output"][];
            /** Loopback */
            loopback: number;
        } & {
            [key: string]: unknown;
        };
        /** RelayIntent */
        RelayIntent: {
            /**
             * Servers
             * @default []
             */
            servers: components["schemas"]["RelayServer-Input"][];
        };
        /** RelayServer */
        "RelayServer-Input": {
            /** Address */
            address: string;
            /** Service */
            service?: string | null;
            /** Segment */
            segment?: number | null;
        };
        /** RelayServer */
        "RelayServer-Output": {
            /** Address */
            address: string;
            /** Service */
            service?: string;
            /** Segment */
            segment?: number;
        } & {
            [key: string]: unknown;
        };
        /** RoleChange */
        RoleChange: {
            /** Role */
            role: string;
        };
        /** RollbackRequest */
        RollbackRequest: {
            /** Device */
            device?: string | null;
            /** Expected */
            expected: number;
        };
        /** Routing */
        Routing: {
            /** Gateway */
            gateway: string;
            /** Peers */
            peers: number[];
            /** Firewall */
            firewall: string | null;
            relay?: components["schemas"]["Relay"];
            ipv6?: components["schemas"]["SegmentIpv6"];
        };
        /** RoutingIntent */
        RoutingIntent: {
            /** Routed */
            routed: boolean;
            /**
             * Prefix Length
             * @default 24
             */
            prefix_length: number;
            /**
             * Firewall
             * @default default
             */
            firewall: string | null;
            /**
             * Peers
             * @default []
             */
            peers: number[];
        };
        /** Segment */
        Segment: {
            /** Fabric */
            fabric: string | null;
            /** Name */
            name: string;
            /** Vni */
            vni: number;
            /** Bd Id */
            bd_id: number | null;
            routing: components["schemas"]["Routing"] | null;
            /** Attachments */
            attachments: components["schemas"]["Attachment"][];
        };
        /** SegmentIpv6 */
        SegmentIpv6: {
            /** Gateway */
            gateway: string;
            /**
             * Hosts
             * @enum {string}
             */
            hosts: "slaac" | "dhcpv6" | "none";
            /** Servers */
            servers: components["schemas"]["RelayServer-Output"][];
        };
        /** Service */
        Service: {
            /** Name */
            name: string;
            /** Vrf */
            vrf: string;
            /** Rd Vlan */
            rd_vlan: number | null;
            /** Device */
            device: string | null;
            /** Port */
            port: string | null;
            /** Vlan */
            vlan: number | null;
            /** Address */
            address: string | null;
            /** Next Hop */
            next_hop: string | null;
            /** Asn */
            asn: number | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "firewall" | "isp";
            /** Devices */
            devices: (string | null)[];
            /** Addresses */
            addresses: {
                [key: string]: string;
            } | null;
            ipv6: components["schemas"]["ServiceIpv6-Output"] | null;
        };
        /** ServiceChange */
        ServiceChange: {
            /** Asn */
            asn: number | null;
        };
        /** ServiceIpv6 */
        "ServiceIpv6-Input": {
            /** Address */
            address: string;
            /** Next Hop */
            next_hop: string;
            /** Addresses */
            addresses?: {
                [key: string]: string;
            } | null;
        };
        /** ServiceIpv6 */
        "ServiceIpv6-Output": {
            /** Address */
            address: string;
            /** Next Hop */
            next_hop: string;
            /** Addresses */
            addresses?: {
                [key: string]: string;
            };
        } & {
            [key: string]: unknown;
        };
        /** SnmpPasswords */
        SnmpPasswords: {
            /** Auth */
            auth: string;
            /** Priv */
            priv: string;
        };
        /** SnmpReader */
        SnmpReader: {
            /**
             * Auth
             * @default sha2-256
             */
            auth: string;
            /**
             * Priv
             * @default aes256
             */
            priv: string;
        };
        /** SnmpTrap */
        SnmpTrap: {
            /** Address */
            address: string;
            /** Reader */
            reader: string;
        };
        /** StaticRoute */
        StaticRoute: {
            /** Vpn */
            vpn?: string | null;
            /** Prefix */
            prefix: string;
            /** Next Hop */
            next_hop: string | "NULL0";
            /** Preference */
            preference?: number | null;
        };
        /** SwitchAccount */
        SwitchAccount: {
            /** Level */
            level: number;
            /** Password */
            password: string;
        };
        /** SwitchLoginSetting */
        SwitchLoginSetting: {
            /** Enabled */
            enabled: boolean;
            /**
             * Domain
             * @default radius
             */
            domain: string;
            /**
             * Servers
             * @default []
             */
            servers: string[];
            /** Key */
            key?: string | null;
        };
        /** SyslogSettings */
        SyslogSettings: {
            /** Enabled */
            enabled: boolean;
            /** Host */
            host?: string | null;
            /** Port */
            port?: number | null;
        };
        /** TargetSave */
        TargetSave: {
            routing: components["schemas"]["DeviceRouting"];
            library?: components["schemas"]["LibraryChange"] | null;
        };
        /** UserChange */
        UserChange: {
            /** Role */
            role?: string | null;
            /** Password */
            password?: string | null;
            /** Fabrics */
            fabrics?: {
                [key: string]: boolean;
            } | null;
        };
        /** UsualWindow */
        UsualWindow: {
            /** Days */
            days: string[];
            /** Start */
            start: string;
            /** Minutes */
            minutes: number;
            /** Zone */
            zone: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
        /** ZtpRegistration */
        ZtpRegistration: {
            /** Mac */
            mac: string;
            /** Address */
            address: string;
            /** Gateway */
            gateway: string;
            /**
             * Interface
             * @default MEth0/0/0
             */
            interface: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Health"];
                };
            };
        };
    };
    device_config_devices__name__config_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    devices_devices_get: {
        parameters: {
            query?: {
                site?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Device"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_topology_topology_get: {
        parameters: {
            query?: {
                site?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_interfaces_devices__name__interfaces_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    overview_overview_get: {
        parameters: {
            query?: {
                site?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_traffic_traffic_get: {
        parameters: {
            query?: {
                site?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_control_plane_devices__name__control_plane_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_hardware_devices__name__hardware_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    interface_history_devices__name__history_get: {
        parameters: {
            query: {
                interface: string;
                minutes?: number;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_diff_jobs_diff_jobs_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiffJob"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    start_diff_job_diff_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["Body_start_diff_job_diff_jobs_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobId"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_diff_job_diff_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiffJob"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_latest_diff_devices__name__diff_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiffSnapshot"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    drift_status_drift_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Drift"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    login_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_login_auth_login_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    setup_needed_setup_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    first_admin_setup_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_first_admin_setup_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    logout_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    me_auth_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Me"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_my_password_auth_password_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_change_my_password_auth_password_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_settings_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_setting_settings__key__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                key: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_setting_settings__key__put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reset_setting_settings__key__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                key: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_images_images_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    upload_image_images_post: {
        parameters: {
            query: {
                name: string;
                sha256?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_image_images__image_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                image_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    directory_view_directory_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    directory_test_directory_test_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DirectoryTest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    alert_receiver_alerts_receiver_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_alert_alerts_test_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_users_users_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_user_users_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewUser"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_user_users__name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_user_users__name__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UserChange"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_deploy_jobs_deploy_jobs_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeployJob"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_deploy_deploy_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["Body_request_deploy_deploy_jobs_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobId"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_deploy_deploy_jobs__job_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["Body_approve_deploy_deploy_jobs__job_id__approve_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobId"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_deploy_deploy_jobs__job_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobId"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_deploy_job_deploy_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeployJob"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_upgrade_jobs_upgrade_jobs_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_upgrade_upgrade_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_request_upgrade_upgrade_jobs_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_upgrade_upgrade_jobs__job_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["Body_approve_upgrade_upgrade_jobs__job_id__approve_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_upgrade_upgrade_jobs__job_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_upgrade_job_upgrade_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    audit_trail_audit_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_change_sets_fabrics__site__change_sets_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_change_set_fabrics__site__change_sets_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_change_set_fabrics__site__change_sets__change_set_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                change_set_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discard_change_set_fabrics__site__change_sets__change_set_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                change_set_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    deploy_change_set_fabrics__site__change_sets__change_set_id__deploy_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                change_set_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_segments_segments_get: {
        parameters: {
            query: {
                site: string;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Segment"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_segment_segments_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_create_segment_segments_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    attachable_ports_devices__name__ports_get: {
        parameters: {
            query?: {
                services?: boolean;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_faceplate_devices__name__faceplate_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    next_bd_id_segments_next_bd_id_get: {
        parameters: {
            query: {
                site: string;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_segment_segments__site___vni__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rename_segment_segments__site___vni__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_rename_segment_segments__site___vni__patch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    attach_segment_segments__site___vni__attachments_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_attach_segment_segments__site___vni__attachments_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    detach_segment_segments__site___vni__attachments_delete: {
        parameters: {
            query: {
                device: string;
                interface: string;
            };
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_attachment_segments__site___vni__attachments_patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_change_attachment_segments__site___vni__attachments_patch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_services_services_get: {
        parameters: {
            query: {
                site: string;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Service"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_service_services_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_create_service_services_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_service_services__site___name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_service_services__site___name__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ServiceChange"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_service_ipv6_services__site___name__ipv6_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ServiceIpv6-Input"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    activate_device_devices__name__management_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Activation"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decommission_device_devices__name__management_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    build_options_fabrics__site__build_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_switches_fabrics__site__switches_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewSwitches"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_address_plan_fabrics_address_plan_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AddressPlanRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_fabrics_fabrics_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Fabric"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_fabric_fabrics_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewFabric"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_inventory_inventory_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_inventory_inventory_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InventorySwitch"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    inventory_models_inventory_models_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": string[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_inventory_inventory__serial__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                serial: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InventorySwitch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_inventory_inventory__serial__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                serial: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_spine_fabrics__site__spines_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewSpine"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_leaf_pair_fabrics__site__leaf_pairs_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewLeafPair"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    removal_plan_devices__name__removal_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_device_devices__name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fetch_hostkeys_devices__name__hostkeys_fetch_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pin_hostkeys_devices__name__hostkeys_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApprovedKeys"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    forget_hostkeys_devices__name__hostkeys_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_ztp_ztp_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    register_ztp_devices__name__ztp_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ZtpRegistration"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    unregister_ztp_devices__name__ztp_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ztp_files_devices__name__ztp_files_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_ztp_devices__name__ztp_approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApprovedKeys"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    switch_inventory_state_devices__name__inventory_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_switch_inventory_devices__name__inventory_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_racks_fabrics__site__racks_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_rack_fabrics__site__racks_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewRack"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_rack_fabrics__site__racks_delete: {
        parameters: {
            query: {
                location: string;
                rack: string;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    place_device_devices__name__location_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Placement"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_device_role_devices__name__role_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoleChange"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_config_versions_devices__name__configs_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    back_up_config_devices__name__configs_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    show_config_version_devices__name__configs__version_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                version_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    diff_config_versions_devices__name__configs__older__diff__newer__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                older: number;
                newer: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_intent_snapshots_fabrics__site__intent_snapshots_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_intent_snapshot_fabrics__site__intent_snapshots__snapshot_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                snapshot_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_rollback_fabrics__site__intent_snapshots__snapshot_id__rollback_get: {
        parameters: {
            query?: {
                device?: string | null;
            };
            header?: never;
            path: {
                site: string;
                snapshot_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    apply_rollback_fabrics__site__intent_snapshots__snapshot_id__rollback_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                snapshot_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RollbackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_checks_fabrics__site__checks_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_health_fabrics__site__health_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_capacity_fabrics__site__capacity_get: {
        parameters: {
            query?: {
                days?: number;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    port_capacity_devices__name__capacity_get: {
        parameters: {
            query: {
                interface: string;
                days?: number;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    timeline_events_get: {
        parameters: {
            query?: {
                site?: string | null;
                device?: string | null;
                kind?: string | null;
                since?: string | null;
                until?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_intent_fabrics__site__intent_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/yaml": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_intent_fabrics__site__intent_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IntentText"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntentPlan"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_intent_fabrics__site__intent_import_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IntentImport"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntentImported"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_trunks_devices__name__trunks_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_trunk_devices__name__trunks_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NewTrunk"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_trunk_devices__name__trunks__trunk__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                trunk: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_routing_segments__site___vni__routing_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoutingIntent"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_relay_segments__site___vni__relay_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RelayIntent"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_segment_ipv6_segments__site___vni__ipv6_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Ipv6Intent"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_ipv6_pool_fabrics__site__ipv6_pool_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_ipv6_pool_fabrics__site__ipv6_pool_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_pip_pool_fabrics__site__pip_pool_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_pip_pool_fabrics__site__pip_pool_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_relay_pool_fabrics__site__relay_pool_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_relay_pool_fabrics__site__relay_pool_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_architecture_fabrics__site__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_fabric_fabrics__site__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_delete_fabric_fabrics__site__delete"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_snmp_fabrics__site__snmp_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_snmp_fabrics__site__snmp_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["Body_set_fabric_snmp_fabrics__site__snmp_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_gateway_mac_fabrics__site__gateway_mac_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_fabric_gateway_mac_fabrics__site__gateway_mac_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_deletion_fabrics__site__deletion_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_usual_window_fabrics__site__usual_window_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_usual_window_fabrics__site__usual_window_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["UsualWindow"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_deploys_fabrics__site__deploys_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_fabric_deploys_fabrics__site__deploys_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_ntp_fabrics__site__ntp_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NtpSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_overrides_fabrics__site__overrides_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_override_fabrics__site__overrides_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OverridePath"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_override_fabrics__site__overrides__path__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                path: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OverrideValue"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    make_standard_fabrics__site__overrides__path__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                path: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_fabric_ospf_auth_fabrics__site__ospf_auth_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_ospf_auth_fabrics__site__ospf_auth_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OspfAuthSetting"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    roll_fabric_ospf_key_fabrics__site__ospf_auth_roll_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OspfAuthRoll"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_fabric_switch_login_fabrics__site__switch_login_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_switch_login_fabrics__site__switch_login_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SwitchLoginSetting"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_switch_account_fabrics__site__switch_accounts__name__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SwitchAccount"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_switch_account_fabrics__site__switch_accounts__name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_netstream_fabrics__site__netstream_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NetStreamSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_syslog_fabrics__site__syslog_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SyslogSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_snmp_v2c_fabrics__site__snmp_v2c_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_set_fabric_snmp_v2c_fabrics__site__snmp_v2c_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_snmp_reader_fabrics__site__snmp_readers__name__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SnmpReader"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_fabric_snmp_reader_fabrics__site__snmp_readers__name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_snmp_passwords_fabrics__site__snmp_readers__name__passwords_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SnmpPasswords"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_fabric_snmp_trap_fabrics__site__snmp_traps__name__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SnmpTrap"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_fabric_snmp_trap_fabrics__site__snmp_traps__name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    copy_fabric_snmp_fabrics__site__snmp_copy_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_copy_fabric_snmp_fabrics__site__snmp_copy_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_snmp_state_fabrics__site__snmp_state_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_snmp_password_jobs_snmp_password_jobs_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_snmp_passwords_snmp_password_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_request_snmp_passwords_snmp_password_jobs_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_snmp_passwords_snmp_password_jobs__job_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_snmp_password_job_snmp_password_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_microsegmentation_fabrics__site__microsegmentation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_microsegmentation_devices__name__microsegmentation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_microsegmentation_mode_fabrics__site__microsegmentation_mode_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MicrosegMode"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_microseg_group_fabrics__site__microsegmentation_groups_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MicrosegGroup"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_microseg_group_fabrics__site__microsegmentation_groups__epg__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                epg: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MicrosegGroup"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_microseg_group_fabrics__site__microsegmentation_groups__epg__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                epg: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_microseg_inside_fabrics__site__microsegmentation_epgs__epg__inside_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                epg: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MicrosegInside"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_microseg_contract_fabrics__site__microsegmentation_contracts_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MicrosegContract"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_microseg_contract_fabrics__site__microsegmentation_contracts__source___destination__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                source: number;
                destination: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_qos_fabrics__site__qos_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_qos_enabled_fabrics__site__qos_enabled_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QosEnabled"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_qos_segment_fabrics__site__qos_segments__vni__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QosMark"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_qos_segment_fabrics__site__qos_segments__vni__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                vni: number;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_qos_port_fabrics__site__qos_ports_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QosPortMark"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_port_settings_fabrics__site__port_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    port_settings_adoption_fabrics__site__port_settings_adoption_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_port_settings_enabled_fabrics__site__port_settings_enabled_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PortSettingsEnabled"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_port_setting_devices__name__port_settings__port__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                port: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_port_setting_devices__name__port_settings__port__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                port: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PortSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_protection_fabrics__site__protection_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_protection_mode_fabrics__site__protection_mode_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProtectionMode"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_port_protection_devices__name__protection__port__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                port: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_port_protection_devices__name__protection__port__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                port: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProtectionTemplate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_dci_dci_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_dci_link_dci_links__name__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DciLink"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_dci_link_dci_links__name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_dci_segment_dci_links__name__segments__segment__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                segment: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DciSegment"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_dci_segment_dci_links__name__segments__segment__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
                segment: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_routing_intent_routing_get: {
        parameters: {
            query: {
                site: string;
            };
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    routing_targets_routing__site__targets_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_device_routing_routing__site__devices__device__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                device: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeviceRouting"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_target_routing_routing__site__targets__target__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                target: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TargetSave"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    promote_routing_library_routing__site__library_promote_post: {
        parameters: {
            query?: {
                apply?: boolean;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    routing_library_routing__site__library_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    routing_imports_routing__site__imports_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_routing_routing__site__devices__device__import_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
                device: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_flows_fabrics__site__flows_get: {
        parameters: {
            query?: {
                minutes?: number;
                kind?: string;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_flows_devices__name__flows_get: {
        parameters: {
            query?: {
                minutes?: number;
                kind?: string;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_logs_devices__name__logs_get: {
        parameters: {
            query?: {
                severity?: number;
                text?: string;
                hours?: number;
                limit?: number;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fabric_logs_fabrics__site__logs_get: {
        parameters: {
            query?: {
                severity?: number;
                text?: string;
                hours?: number;
                limit?: number;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    path_trace_fabrics__site__trace_get: {
        parameters: {
            query: {
                source: string;
                destination: string;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reachability_fabrics__site__reach_get: {
        parameters: {
            query: {
                from: string;
                to: string;
                proto?: string | null;
                port?: number | null;
            };
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reach_hosts_fabrics__site__reach_hosts_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                site: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_routes_devices__name__routes_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_bgp_devices__name__bgp_get: {
        parameters: {
            query?: {
                vpn?: string | null;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    device_bgp_simulation_devices__name__bgp_simulation_get: {
        parameters: {
            query: {
                peer: string;
                vpn?: string | null;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    start_import_imports_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportStart"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_import_imports__import_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    drop_import_imports__import_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_import_keys_imports__import_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportApproval"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_import_imports__import_id__read_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    detect_import_imports__import_id__detect_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_import_imports__import_id__preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportChoices"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    write_import_imports__import_id__write_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: {
                fabric_session?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportChoices"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
