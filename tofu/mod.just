set default-list
set default-script
set quiet
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[confirm('Apply OpenTofu stack ' + stack + ' [y|N] ?')]
[doc('Apply an OpenTofu stack')]
apply stack *args:
    just tofu run "{{ stack }}" apply {{ args }}

[doc('Bootstrap the Cadi VPS with Ansible')]
cadi-bootstrap *args:
    ip="$(just tofu output hetzner/cadi -raw server_ip)"
    port=22
    if ssh -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new -p 22222 "ubuntu@${ip}" true 2>/dev/null; then
        port=22222
    fi
    just log info "Running Ansible against Cadi" "ip" "${ip}" "port" "${port}"
    ansible-playbook -i "${ip}," -u ubuntu -e "bootstrap_ssh_port=${port}" \
        "{{ source_directory() }}/../ansible/cadi/playbook.yaml" {{ args }}

[doc('Initialize an OpenTofu stack')]
init stack *args:
    just tofu run "{{ stack }}" init {{ args }}

[doc('Show the outputs of an OpenTofu stack')]
output stack *args:
    just tofu run "{{ stack }}" output {{ args }}

[doc('Plan an OpenTofu stack')]
plan stack *args:
    just tofu run "{{ stack }}" plan {{ args }}

# Provider credentials are 1Password references in <provider>/.env.op (the first path segment of the stack)
[arg('stack', pattern='backblaze|hetzner/cadi')]
[private]
run stack *args:
    exec op run --no-masking --env-file="{{ source_directory() }}/$(cut -d/ -f1 <<< "{{ stack }}")/.env.op" -- \
        tofu -chdir="{{ source_directory() }}/{{ stack }}" {{ args }}
