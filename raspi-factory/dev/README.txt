Put your SSH public key in this folder as `authorized_keys`, then flash with --dev:

    cat ~/.ssh/id_ed25519.pub > dev/authorized_keys      # or id_rsa.pub
    ./prepare-sd.sh --repair --dev

install-factory.sh installs it for the uid-1000 user and turns OFF password
login. Without a key here, --dev still enables SSH but leaves password login
on, which is weaker.

authorized_keys is gitignored — keys stay on your machine.
