The lab need to be started with privilages mdoe

sudo kathara lstart --privileged

Kubernetties verefication commands:

kubectl get nodes
watch "kubectl get nodes"
kubectl get pods -A
watch "kubectl get pods -A"