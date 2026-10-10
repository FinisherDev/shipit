from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import Shipment

User = get_user_model()

SHIPMENT_DATA = {
    'sender_name': 'Ada Obi',
    'receiver_name': 'Chidi Eze',
    'origin': 'Onitsha',
    'destination': 'Lagos',
}


def make_shipment(**overrides):
    return Shipment.objects.create(**{**SHIPMENT_DATA, **overrides})


class ShipmentModelTests(APITestCase):
    def test_tracking_id_is_generated(self):
        shipment = make_shipment()
        self.assertTrue(shipment.tracking_id.startswith('SHIP-'))
        self.assertEqual(len(shipment.tracking_id), 15)

    def test_creating_records_first_history_entry(self):
        shipment = make_shipment()
        self.assertEqual(shipment.history.count(), 1)
        self.assertEqual(shipment.history.first().status, 'Pending')

    def test_saving_again_persists_changes(self):
        # Regression test: save() used to silently skip updates.
        shipment = make_shipment()
        shipment.receiver_name = 'New Name'
        shipment.save()
        shipment.refresh_from_db()
        self.assertEqual(shipment.receiver_name, 'New Name')

    def test_status_change_adds_history_once(self):
        shipment = make_shipment()
        shipment.status = Shipment.Status.IN_TRANSIT
        shipment.save()
        shipment.save()  # no change, so no extra entry
        statuses = list(shipment.history.values_list('status', flat=True))
        self.assertEqual(statuses, ['Pending', 'In Transit'])

    def test_tracking_id_does_not_change_on_update(self):
        shipment = make_shipment()
        original = shipment.tracking_id
        shipment.origin = 'Awka'
        shipment.save()
        self.assertEqual(Shipment.objects.get(pk=shipment.pk).tracking_id, original)


class ShipmentAPITests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff', password='pass12345', is_staff=True)
        self.customer = User.objects.create_user('customer', password='pass12345')
        self.shipment = make_shipment()
        self.url = f'/api/shipments/{self.shipment.tracking_id}/'

    def auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    # --- access control ---
    def test_anonymous_cannot_list_or_read(self):
        self.assertEqual(self.client.get('/api/shipments/').status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_anonymous_cannot_create(self):
        self.assertEqual(self.client.post('/api/shipments/', SHIPMENT_DATA).status_code, 401)

    def test_non_staff_user_is_forbidden(self):
        self.auth(self.customer)
        self.assertEqual(self.client.get('/api/shipments/').status_code, 403)

    def test_token_login(self):
        response = self.client.post('/api/auth/token/', {'username': 'staff', 'password': 'pass12345'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.data)

    # --- staff actions ---
    def test_staff_can_create_shipment(self):
        self.auth(self.staff)
        response = self.client.post('/api/shipments/', SHIPMENT_DATA, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['tracking_id'].startswith('SHIP-'))
        self.assertEqual(len(response.data['history']), 1)

    def test_client_cannot_set_tracking_id(self):
        self.auth(self.staff)
        response = self.client.post(
            '/api/shipments/', {**SHIPMENT_DATA, 'tracking_id': 'HACKED'}, format='json'
        )
        self.assertNotEqual(response.data['tracking_id'], 'HACKED')

    def test_patch_status_with_json_records_history(self):
        self.auth(self.staff)
        response = self.client.patch(self.url, {'status': 'In Transit'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.shipment.history.count(), 2)

    def test_put_with_multipart_updates_all_fields(self):
        self.auth(self.staff)
        data = {**SHIPMENT_DATA, 'destination': 'Abuja', 'status': 'In Transit'}
        response = self.client.put(self.url, data, format='multipart')
        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.destination, 'Abuja')
        self.assertEqual(self.shipment.status, 'In Transit')

    def test_invalid_status_is_rejected(self):
        self.auth(self.staff)
        response = self.client.patch(self.url, {'status': 'Teleported'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_history_endpoint(self):
        self.auth(self.staff)
        self.client.patch(self.url, {'status': 'Delivered'}, format='json')
        response = self.client.get(f'{self.url}history/')
        self.assertEqual([h['status'] for h in response.data], ['Pending', 'Delivered'])

    def test_search_and_filter(self):
        make_shipment(sender_name='Someone Else', status='Delivered')
        self.auth(self.staff)
        search = self.client.get('/api/shipments/', {'search': 'Someone'})
        self.assertEqual(search.data['count'], 1)
        delivered = self.client.get('/api/shipments/', {'status': 'Delivered'})
        self.assertEqual(delivered.data['count'], 1)
